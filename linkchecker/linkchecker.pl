#!/usr/bin/env perl
use strict;
use warnings;

use Getopt::Long;
use Pod::Usage;
use URI;
use LWP::UserAgent;
use HTML::Parser;

my $output_file;
my $root_url_str;
my $skip_file;
my $help;

GetOptions(
    "output|o=s" => \$output_file,
    "root|r=s"   => \$root_url_str,
    "skip|s=s"   => \$skip_file,
    "help|h"     => \$help,
) or pod2usage(2);

pod2usage(1) if $help;

my $start_url_str = shift @ARGV;
die "Usage: $0 [--output results.tsv] <URL>\n" unless $start_url_str;

my $start_uri = URI->new($start_url_str);
die "Invalid starting URL scheme: must be http or https\n" 
    unless $start_uri->scheme && $start_uri->scheme =~ /^https?$/;

# Determine scope prefix
my $scope_prefix;
if ($root_url_str) {
    my $root_uri = URI->new($root_url_str);
    die "Invalid root URL scheme: must be http or https\n"
        unless $root_uri->scheme && $root_uri->scheme =~ /^https?$/;
    $scope_prefix = $root_uri->canonical->as_string;
    $scope_prefix =~ s/#.*$//;
} else {
    $scope_prefix = $start_uri->canonical->as_string;
    $scope_prefix =~ s/#.*$//;
    if ($scope_prefix !~ m{/$}) {
        my $path = $start_uri->path;
        if ($path =~ m{/[^/]*\.[^/]+$}) {
            $scope_prefix =~ s{/[^/]*$}{/};
        } else {
            $scope_prefix .= '/' unless $path eq '';
        }
    }
    $scope_prefix = $start_uri->canonical->as_string unless $scope_prefix; # fallback
}

my @skip_prefixes;
if ($skip_file) {
    open my $sfh, '<', $skip_file or die "Cannot open skip file $skip_file: $!\n";
    while (my $line = <$sfh>) {
        $line =~ s/^\s+|\s+$//g;
        push @skip_prefixes, $line if $line ne '';
    }
    close $sfh;
}

my %visited;          # url => status_string
my %page_ids;         # url => \%ids
my @results;          # result rows
my %page_ok;          # url => 1 or 0
my %page_spidered;    # url => 1

my $ua = LWP::UserAgent->new(
    timeout      => 10,
    max_redirect => 0, # Manual redirect handling
    agent        => 'LinkChecker-PD/1.0',
    ssl_opts     => { verify_hostname => 0 },
);

sub classify_error {
    my ($response) = @_;
    my $msg = $response->status_line;
    if ($response->header('Client-Warning') && $response->header('Client-Warning') eq 'Internal response') {
        if ($msg =~ /Can't connect/i) { return "Connection failed: $msg"; }
        if ($msg =~ /read timeout/i) { return "Connection timeout (10s)"; }
        if ($msg =~ /SSL connect attempt failed/i) { return "SSL error: $msg"; }
        if ($msg =~ /Can't resolve host/i) { return "DNS resolution failed"; }
        return "Error: $msg";
    }
    return $msg;
}

sub emit_result {
    my ($src, $line, $dest, $outcome) = @_;
    $src //= '(unknown)';
    $line //= 'NA';
    print "[$src:$line] -> $dest ... $outcome\n";
    push @results, { src => $src, line => $line, dest => $dest, outcome => $outcome };
    $page_ok{$src} = 0 if $outcome !~ /^2/ && $outcome !~ /^3/ && $outcome !~ /OK/ && $outcome ne 'Skipped';
}

sub is_internal {
    my ($url) = @_;
    return 0 unless $url;
    my $u = URI->new($url)->canonical;
    return 1 if index($u->as_string, $scope_prefix) == 0;
    return 0;
}

sub normalize_url {
    my ($link_str, $base_str) = @_;
    my $u = URI->new_abs($link_str, $base_str)->canonical;
    return (undef, undef) unless $u->scheme && $u->scheme =~ /^https?$/;
    my $frag = $u->fragment;
    $u->fragment(undef);
    return ($u->as_string, $frag);
}

sub fetch_with_redirects {
    my ($src_url, $src_line, $start_target_url, $method) = @_;
    my $current_target = $start_target_url;
    my $hops = 0;
    my $res;

    while ($hops < 10) {
        my $req;
        if ($method eq 'HEAD') {
            $req = HTTP::Request->new(HEAD => $current_target);
        } else {
            $req = HTTP::Request->new(GET => $current_target);
        }
        $res = $ua->request($req);
        
        my $code = $res->code;
        if ($method eq 'HEAD' && $code == 405) {
            $method = 'GET';
            next;
        }

        if ($res->is_redirect) {
            my $loc = $res->header('Location');
            my $loc_abs = URI->new_abs($loc, $current_target)->canonical->as_string if $loc;
            $loc_abs //= "?";
            my $outcome = $res->status_line . " -> " . $loc_abs;
            $visited{$current_target} = $outcome unless exists $visited{$current_target};
            emit_result($src_url, $src_line, $current_target, $outcome) if defined $src_url;
            
            $src_url = $current_target;
            $src_line = 'NA';
            
            $current_target = $loc_abs;
            $hops++;
            $method = 'GET'; # subsequent requests always GET
        } else {
            my $outcome = classify_error($res);
            $visited{$current_target} = $outcome unless exists $visited{$current_target};
            return ($res, $current_target, $outcome, $src_url, $src_line);
        }
    }

    my $outcome = "Too many redirects (limit: 10)";
    $visited{$current_target} = $outcome unless exists $visited{$current_target};
    return (undef, $current_target, $outcome, $src_url, $src_line);
}

sub parse_html_content {
    my ($url, $content) = @_;
    my @links;
    my %ids;

    my $p = HTML::Parser->new(
        api_version => 3,
        start_h => [sub {
            my ($tag, $attr, $line) = @_;
            # Collect links
            if ($tag eq 'a' && $attr->{href}) { push @links, { url => $attr->{href}, line => $line }; }
            elsif ($tag eq 'img' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'script' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'link' && $attr->{href}) { push @links, { url => $attr->{href}, line => $line }; }
            elsif ($tag eq 'iframe' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'frame' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'source' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'video' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'audio' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'object' && $attr->{data}) { push @links, { url => $attr->{data}, line => $line }; }
            elsif ($tag eq 'embed' && $attr->{src}) { push @links, { url => $attr->{src}, line => $line }; }
            elsif ($tag eq 'form' && $attr->{action}) { push @links, { url => $attr->{action}, line => $line }; }
            
            # Collect ids
            $ids{$attr->{id}} = 1 if defined $attr->{id};
            $ids{$attr->{name}} = 1 if $tag eq 'a' && defined $attr->{name};
        }, "tagname, attr, line"],
    );
    $p->parse($content);
    $p->eof;
    
    $page_ids{$url} = \%ids;
    return \@links;
}

sub crawl {
    my ($start_url_raw) = @_;
    my ($init_url, $ignored_frag) = normalize_url($start_url_raw, $start_url_raw);
    
    # We maintain a queue for breadth-first traversal
    my @queue = ($init_url);
    
    while (my $url = shift @queue) {
        next if $page_spidered{$url};
        $page_spidered{$url} = 1;
        $page_ok{$url} = 1 unless defined $page_ok{$url};

        my ($res, $final_url, $outcome, $start_src, $start_line) = fetch_with_redirects(undef, undef, $url, 'GET');
        
        if ($outcome !~ /200 OK/ && $outcome !~ /OK/) {
            $page_ok{$url} = 0;
            $page_ok{$final_url} = 0 if $final_url ne $url;
            print "[START:NA] -> $final_url ... $outcome\n";
        }

        if ($final_url ne $url) {
            $page_spidered{$final_url} = 1;
            $page_ok{$final_url} = 1 unless defined $page_ok{$final_url};
        }

        next unless $res && $res->is_success;
        next unless $res->header('Content-Type') && $res->header('Content-Type') =~ /html/i;

        my $links = parse_html_content($final_url, $res->decoded_content);
        
        my @fragments_to_check;

        for my $l (@$links) {
            my ($target_url, $frag) = normalize_url($l->{url}, $final_url);
            next unless $target_url; # Handle non-http schemes
            
            my $full_target = $frag ? "$target_url#$frag" : $target_url;
            my $is_skipped = 0;
            for my $prefix (@skip_prefixes) {
                if (index($full_target, $prefix) == 0) {
                    $is_skipped = 1;
                    last;
                }
            }
            if ($is_skipped) {
                $visited{$target_url} = "Skipped" unless exists $visited{$target_url};
                emit_result($final_url, $l->{line}, $full_target, "Skipped");
                next;
            }

            my $target_is_internal = is_internal($target_url);

            if (!exists $visited{$target_url} || $visited{$target_url} eq 'Skipped') {
                # New URL, need to fetch it
                my $target_method = $target_is_internal ? 'GET' : 'HEAD';
                my ($f_res, $f_final, $f_out, $f_src_url, $f_src_line) = fetch_with_redirects($final_url, $l->{line}, $target_url, $target_method);
                
                if ($f_out) {
                    emit_result($f_src_url, $f_src_line, $f_final, $f_out);
                }

                if ($target_is_internal && $f_res && $f_res->is_success) {
                    # If it's internal and an HTML page, parse it.
                    if ($f_res->header('Content-Type') && $f_res->header('Content-Type') =~ /html/i) {
                        parse_html_content($f_final, $f_res->decoded_content);
                        push @queue, $f_final unless $page_spidered{$f_final};
                    } else {
                        $page_ids{$f_final} = {}; # Not HTML, no IDs
                    }
                }
            } else {
                # Already visited (or at least we started fetching it in a redirect chain)
                my $cached_out = $visited{$target_url} || "200 OK"; 
                emit_result($final_url, $l->{line}, $target_url, $cached_out);
            }

            # Check fragments
            if ($frag && $target_is_internal) {
                push @fragments_to_check, { src => $final_url, line => $l->{line}, dest => $target_url, frag => $frag };
            }
        }

        # Validate fragments after the page is fully processed
        for my $fc (@fragments_to_check) {
            my $dest = $fc->{dest};
            my $frag = $fc->{frag};
            if (exists $page_ids{$dest}) {
                if (!$page_ids{$dest}->{$frag}) {
                    emit_result($fc->{src}, $fc->{line}, "$dest#$frag", "Anchor #$frag not found");
                }
            } else {
                # Could be a resource like a PDF which doesn't have IDs
                # Or a fetching failure
                if (($visited{$dest} // "Error") =~ /200 OK/) {
                    # emit_result($fc->{src}, $fc->{line}, "$dest#$frag", "Anchor validation unsupported for non-HTML/failed targets");
                }
            }
        }
    }
}

print "Starting crawl of $start_url_str\n";
crawl($start_url_str);

print "\n=== Summary ===\n";
my $total = 0;
my $broken_count = 0;
for my $url (sort keys %page_ok) {
    if (exists $page_spidered{$url} && $page_spidered{$url} == 1) {
        $total++;
        my $status = $page_ok{$url} ? "OK" : "NOT OK";
        if (!$page_ok{$url}) {
            $broken_count++;
        }
        printf "%-50s %s\n", $url, $status;
    }
}
print "---\n";
print "Total pages spidered: $total\n";

if ($output_file) {
    open my $fh, '>', $output_file or die "Cannot open output file $output_file: $!";
    print $fh "Source URL\tLine\tDestination URL\tStatus\n";
    for my $r (@results) {
        print $fh "$r->{src}\t$r->{line}\t$r->{dest}\t$r->{outcome}\n";
    }
    close $fh;
    print "\nResults written to $output_file\n";
}

=head1 NAME

linkchecker.pl - A recursive link checker

=head1 SYNOPSIS

linkchecker.pl [options] <URL>

 Options:
   -o, --output FILE    Tab-delimited output file
   -r, --root URL       Limit spidering to URLs under this root
   -s, --skip FILE      File containing line-separated URL prefixes to skip
   -h, --help           Print this help message

=cut
