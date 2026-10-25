#!/usr/bin/env perl
# amrdiff.pl - Highlight column differences and row differences in tab-delimited files.
# Visual style: Foreground-only color highlighting, unchanged context dimmed, pure ASCII.
#
# Usage:
#   amrdiff.pl [options] <file1> <file2>
#
# Options:
#   -C, --context <N>     Number of context lines around differences (default: 2)
#   -y, --side-by-side    Display differences side-by-side (like vimdiff)
#       --html            Output standalone HTML instead of terminal ANSI
#       --no-color        Disable ANSI escape colors (plain text)
#   -h, --help            Show this help message

use strict;
use warnings;
use Getopt::Long qw(GetOptionsFromArray);
use Algorithm::Diff qw(sdiff);

my $context_lines = 2;
my $side_by_side  = 0;
my $html_output   = 0;
my $no_color      = 0;
my $help          = 0;

GetOptions(
    'context|C=i'    => \$context_lines,
    'side-by-side|y' => \$side_by_side,
    'html'           => \$html_output,
    'no-color'       => \$no_color,
    'help|h'         => \$help,
) or usage(1);

usage(0) if $help;

if (@ARGV != 2) {
    warn "Error: Exactly two input files required.\n";
    usage(1);
}

my ($file1, $file2) = @ARGV;
open my $fh1, '<', $file1 or die "Cannot open '$file1': $!\n";
open my $fh2, '<', $file2 or die "Cannot open '$file2': $!\n";

my $header1 = <$fh1>;
my $header2 = <$fh2>;

die "File '$file1' is empty.\n" unless defined $header1;
die "File '$file2' is empty.\n" unless defined $header2;

$header1 =~ s/\r?\n$//;
$header2 =~ s/\r?\n$//;

my @hdr_fields1 = split /\t/, $header1, -1;
my @hdr_fields2 = split /\t/, $header2, -1;

my @lines1;
while (my $line = <$fh1>) {
    $line =~ s/\r?\n$//;
    push @lines1, $line;
}
close $fh1;

my @lines2;
while (my $line = <$fh2>) {
    $line =~ s/\r?\n$//;
    push @lines2, $line;
}
close $fh2;

# Diff lines using sdiff (LCS based alignment)
# Each item: [ $op, $line1, $line2 ]
# $op is 'u' (unchanged), 'c' (changed), '-' (deleted from file1), '+' (added to file2)
my @diff_items = sdiff(\@lines1, \@lines2);

# Determine which items are within context range of differences
my $total = scalar @diff_items;
my @is_diff = map { $_->[0] ne 'u' ? 1 : 0 } @diff_items;
my @show    = (0) x $total;

if ($context_lines < 0) {
    # If negative, show all lines
    @show = (1) x $total;
} else {
    for my $i (0 .. $total - 1) {
        if ($is_diff[$i]) {
            my $start = $i - $context_lines;
            $start = 0 if $start < 0;
            my $end = $i + $context_lines;
            $end = $total - 1 if $end >= $total;
            for my $j ($start .. $end) {
                $show[$j] = 1;
            }
        }
    }
}

# Calculate maximum width for each column across all displayed rows and headers
my @col_widths;
my sub update_col_widths {
    my ($fields_ref) = @_;
    for my $idx (0 .. $#$fields_ref) {
        my $len = length($fields_ref->[$idx]);
        if (!defined $col_widths[$idx] || $len > $col_widths[$idx]) {
            $col_widths[$idx] = $len;
        }
    }
}

update_col_widths(\@hdr_fields1);
update_col_widths(\@hdr_fields2);

for my $i (0 .. $total - 1) {
    next unless $show[$i];
    my ($op, $l1, $l2) = @{ $diff_items[$i] };
    if ($op eq 'u' || $op eq 'c') {
        my @f1 = split /\t/, $l1, -1;
        my @f2 = split /\t/, $l2, -1;
        update_col_widths(\@f1);
        update_col_widths(\@f2);
    } elsif ($op eq '-') {
        my @f1 = split /\t/, $l1, -1;
        update_col_widths(\@f1);
    } elsif ($op eq '+') {
        my @f2 = split /\t/, $l2, -1;
        update_col_widths(\@f2);
    }
}

# Color definitions
my %ANSI = (
    del    => "\e[38;5;203m",       # light red
    del_ch => "\e[38;5;203;1;4m",   # bold underlined red
    add    => "\e[38;5;77m",        # light green
    add_ch => "\e[38;5;77;1;4m",    # bold underlined green
    dim    => "\e[38;5;244m",       # dimmed gray
    hdr    => "\e[1;37m",           # bold white
    gut    => "\e[38;5;240m",       # darker gray for gutters
    fold   => "\e[38;5;244m",       # gray for folds
    reset  => "\e[0m",
);
my %HAS_ATTR = ( del_ch => 1, add_ch => 1, hdr => 1 );

sub esc_html {
    my ($str) = @_;
    $str =~ s/&/&amp;/g;
    $str =~ s/</&lt;/g;
    $str =~ s/>/&gt;/g;
    return $str;
}

sub print_line {
    my ($tokens_ref) = @_;
    return unless $tokens_ref && @$tokens_ref;

    # Merge adjacent tokens with identical style
    my @merged;
    for my $tok (@$tokens_ref) {
        my ($txt, $style) = @$tok;
        next if !defined $txt || $txt eq '';
        $style //= 'plain';
        if (@merged && $merged[-1][1] eq $style) {
            $merged[-1][0] .= $txt;
        } else {
            push @merged, [ $txt, $style ];
        }
    }

    if ($html_output) {
        for my $tok (@merged) {
            my ($txt, $style) = @$tok;
            my $esc = esc_html($txt);
            if ($style eq 'plain') {
                print $esc;
            } else {
                print "<span class=\"$style\">$esc</span>";
            }
        }
        print "\n";
        return;
    }

    if ($no_color) {
        for my $tok (@merged) {
            print $tok->[0];
        }
        print "\n";
        return;
    }

    # State tracking: emit ANSI codes only when styling changes
    my $cur_style = 'plain';
    for my $tok (@merged) {
        my ($txt, $style) = @$tok;
        if ($style eq $cur_style) {
            print $txt;
        } else {
            if ($style eq 'plain') {
                print $ANSI{reset};
            } elsif ($HAS_ATTR{$cur_style} && !$HAS_ATTR{$style}) {
                print $ANSI{reset} . $ANSI{$style};
            } else {
                print $ANSI{$style};
            }
            $cur_style = $style;
            print $txt;
        }
    }
    # Reset at end of line to prevent color bleed
    if ($cur_style ne 'plain') {
        print $ANSI{reset};
    }
    print "\n";
}

# Prefix and suffix diff within a changed cell
sub diff_cell_chars {
    my ($str1, $str2) = @_;
    my $len1 = length($str1);
    my $len2 = length($str2);

    my $pre = 0;
    while ($pre < $len1 && $pre < $len2 && substr($str1, $pre, 1) eq substr($str2, $pre, 1)) {
        $pre++;
    }

    my $suf = 0;
    while ($suf < $len1 - $pre && $suf < $len2 - $pre
           && substr($str1, -$suf - 1, 1) eq substr($str2, -$suf - 1, 1)) {
        $suf++;
    }

    my $pre_str = substr($str1, 0, $pre);
    my $mid1    = substr($str1, $pre, $len1 - $pre - $suf);
    my $mid2    = substr($str2, $pre, $len2 - $pre - $suf);
    my $suf_str = $suf > 0 ? substr($str1, -$suf) : '';

    return ($pre_str, $mid1, $mid2, $suf_str);
}

# Returns list of [ $text, $style ] tokens for a cell
sub cell_tokens {
    my ($col_idx, $val1, $val2, $side, $is_hdr) = @_;
    my $val = ($side eq 'add') ? $val2 : $val1;
    $val = '' unless defined $val;
    my $width = $col_widths[$col_idx] || length($val);
    my $pad_len = $width - length($val);
    $pad_len = 0 if $pad_len < 0;
    my $pad = ' ' x $pad_len;

    if ($is_hdr) {
        return [ [ $val . $pad, 'hdr' ] ];
    }
    if ($side eq 'ctx') {
        return [ [ $val . $pad, 'dim' ] ];
    }
    if (!defined $val1 || !defined $val2 || $val1 eq $val2) {
        if (defined $val1 && defined $val2 && $val1 eq $val2) {
            return [ [ $val . $pad, 'dim' ] ];
        }
        return [ [ $val . $pad, $side ] ];
    }

    my ($pre, $mid1, $mid2, $suf) = diff_cell_chars($val1, $val2);
    my $mid = ($side eq 'add') ? $mid2 : $mid1;
    my $ch_class = $side . '_ch';

    my @toks;
    push @toks, [ $pre, $side ] if length $pre;
    push @toks, [ $mid, $ch_class ] if length $mid;
    push @toks, [ $suf . $pad, $side ] if length($suf . $pad);
    return \@toks;
}

sub row_cells_tokens {
    my ($fields1, $fields2, $side, $is_hdr) = @_;
    my $num_cols = scalar @col_widths;
    my @row_tokens;

    for my $c (0 .. $num_cols - 1) {
        my $v1 = $fields1 ? $fields1->[$c] : undef;
        my $v2 = $fields2 ? $fields2->[$c] : undef;
        my $toks = cell_tokens($c, $v1, $v2, $side, $is_hdr);
        push @row_tokens, @$toks;

        if ($c < $num_cols - 1) {
            my $sep_style;
            if ($is_hdr) {
                $sep_style = 'hdr';
            } elsif ($side eq 'ctx') {
                $sep_style = 'dim';
            } elsif (!defined $v1 || !defined $v2) {
                $sep_style = $side;
            } else {
                my $next_v1 = $fields1 ? $fields1->[$c + 1] : undef;
                my $next_v2 = $fields2 ? $fields2->[$c + 1] : undef;
                my $this_dim = (defined $v1 && defined $v2 && $v1 eq $v2);
                my $next_dim = (defined $next_v1 && defined $next_v2 && $next_v1 eq $next_v2);
                $sep_style = ($this_dim && $next_dim) ? 'dim' : $side;
            }
            push @row_tokens, [ '  ', $sep_style ];
        }
    }
    return @row_tokens;
}

# Total row width for blank filler in side-by-side mode
my $row_plain_width = 0;
for my $w (@col_widths) {
    $row_plain_width += $w + 2;
}
$row_plain_width -= 2 if $row_plain_width > 0;
my $blank_pane = ' ' x $row_plain_width;

# HTML Header
if ($html_output) {
    print <<'HTML_HEAD';
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>amrdiff output</title>
<style>
body {
    background: #14161a;
    color: #d7dae0;
    font-family: ui-monospace, Menlo, Monaco, "Courier New", monospace;
    font-size: 13px;
    line-height: 1.45;
    margin: 1.5rem auto;
    padding: 0 1.5rem;
}
h1 { font-size: 1.15rem; font-weight: bold; margin-bottom: 0.5rem; }
.meta { color: #8b93a1; margin-bottom: 1rem; font-size: 0.9rem; }
.diff-container {
    background: #0d0f12;
    border: 1px solid #262a31;
    border-radius: 6px;
    padding: 0.8rem 1rem;
    overflow-x: auto;
}
pre { margin: 0; white-space: pre; font-family: inherit; }
.del { color: #ff5f5f; }
.del_ch { color: #ff5f5f; font-weight: bold; text-decoration: underline; }
.add { color: #5fd75f; }
.add_ch { color: #5fd75f; font-weight: bold; text-decoration: underline; }
.dim { color: #7d8590; }
.hdr { color: #f0f6fc; font-weight: bold; }
.gut { color: #59606b; }
.fold { color: #7d8590; font-style: italic; }
</style>
</head>
<body>
HTML_HEAD
    print "<h1>amrdiff: " . esc_html($file1) . " vs " . esc_html($file2) . "</h1>\n";
    print "<div class=\"meta\">Options: context=$context_lines, view=" . ($side_by_side ? "side-by-side" : "stacked") . "</div>\n";
    print "<div class=\"diff-container\"><pre>\n";
}

# Render Table Header
if ($side_by_side) {
    my @toks1 = row_cells_tokens(\@hdr_fields1, \@hdr_fields1, 'ctx', 1);
    my @toks2 = row_cells_tokens(\@hdr_fields2, \@hdr_fields2, 'ctx', 1);
    my @hdr_line = (
        [ '     ', 'gut' ],
        @toks1,
        [ ' | ',   'gut' ],
        [ '     ', 'gut' ],
        @toks2,
    );
    print_line(\@hdr_line);
} else {
    my @toks = row_cells_tokens(\@hdr_fields1, \@hdr_fields1, 'ctx', 1);
    my @hdr_line = (
        [ '  ', 'gut' ],
        @toks,
    );
    print_line(\@hdr_line);
}

# Walk through diff items with context tracking and line numbering
my $line_num1 = 2; # Line 1 is the header
my $line_num2 = 2;

my $idx = 0;
while ($idx < $total) {
    if (!$show[$idx]) {
        # Count consecutive folded lines
        my $fold_count = 0;
        while ($idx < $total && !$show[$idx]) {
            my $op = $diff_items[$idx]->[0];
            $line_num1++ if $op eq 'u' || $op eq 'c' || $op eq '-';
            $line_num2++ if $op eq 'u' || $op eq 'c' || $op eq '+';
            $fold_count++;
            $idx++;
        }
        my $plural = $fold_count == 1 ? "line" : "lines";
        my $fold_msg = "... $fold_count identical $plural ...";
        if ($side_by_side) {
            my $w = $row_plain_width + 5; # include gutter width
            my $left_pad = sprintf("%-*s", $w, $fold_msg);
            my $right_pad = sprintf("%-*s", $w, $fold_msg);
            my @line = (
                [ $left_pad,  'fold' ],
                [ ' | ',      'gut' ],
                [ $right_pad, 'fold' ],
            );
            print_line(\@line);
        } else {
            my @line = (
                [ "  $fold_msg", 'fold' ],
            );
            print_line(\@line);
        }
        next;
    }

    my ($op, $l1, $l2) = @{ $diff_items[$idx] };

    if ($side_by_side) {
        my sub gut_tok {
            my ($num) = @_;
            my $s = defined $num ? sprintf("%4d ", $num) : "     ";
            return [ $s, 'gut' ];
        }

        if ($op eq 'u') {
            my @f = split /\t/, $l1, -1;
            my @left_cells  = row_cells_tokens(\@f, \@f, 'ctx', 0);
            my @right_cells = row_cells_tokens(\@f, \@f, 'ctx', 0);
            my @line = (
                gut_tok($line_num1++),
                @left_cells,
                [ ' | ', 'gut' ],
                gut_tok($line_num2++),
                @right_cells,
            );
            print_line(\@line);
        } elsif ($op eq 'c') {
            my @f1 = split /\t/, $l1, -1;
            my @f2 = split /\t/, $l2, -1;
            my @left_cells  = row_cells_tokens(\@f1, \@f2, 'del', 0);
            my @right_cells = row_cells_tokens(\@f1, \@f2, 'add', 0);
            my @line = (
                gut_tok($line_num1++),
                @left_cells,
                [ ' | ', 'gut' ],
                gut_tok($line_num2++),
                @right_cells,
            );
            print_line(\@line);
        } elsif ($op eq '-') {
            my @f1 = split /\t/, $l1, -1;
            my @left_cells = row_cells_tokens(\@f1, undef, 'del', 0);
            my @line = (
                gut_tok($line_num1++),
                @left_cells,
                [ ' | ', 'gut' ],
                gut_tok(undef),
                [ $blank_pane, 'dim' ],
            );
            print_line(\@line);
        } elsif ($op eq '+') {
            my @f2 = split /\t/, $l2, -1;
            my @right_cells = row_cells_tokens(undef, \@f2, 'add', 0);
            my @line = (
                gut_tok(undef),
                [ $blank_pane, 'dim' ],
                [ ' | ', 'gut' ],
                gut_tok($line_num2++),
                @right_cells,
            );
            print_line(\@line);
        }
    } else {
        # Stacked view
        if ($op eq 'u') {
            my @f = split /\t/, $l1, -1;
            my @cells = row_cells_tokens(\@f, \@f, 'ctx', 0);
            my @line = (
                [ '  ', 'dim' ],
                @cells,
            );
            print_line(\@line);
            $line_num1++;
            $line_num2++;
        } elsif ($op eq 'c') {
            my @f1 = split /\t/, $l1, -1;
            my @f2 = split /\t/, $l2, -1;
            my @cells1 = row_cells_tokens(\@f1, \@f2, 'del', 0);
            my @cells2 = row_cells_tokens(\@f1, \@f2, 'add', 0);
            my @line1 = ( [ '< ', 'gut' ], @cells1 );
            my @line2 = ( [ '> ', 'gut' ], @cells2 );
            print_line(\@line1);
            print_line(\@line2);
            $line_num1++;
            $line_num2++;
        } elsif ($op eq '-') {
            my @f1 = split /\t/, $l1, -1;
            my @cells1 = row_cells_tokens(\@f1, undef, 'del', 0);
            my @line = ( [ '< ', 'gut' ], @cells1 );
            print_line(\@line);
            $line_num1++;
        } elsif ($op eq '+') {
            my @f2 = split /\t/, $l2, -1;
            my @cells2 = row_cells_tokens(undef, \@f2, 'add', 0);
            my @line = ( [ '> ', 'gut' ], @cells2 );
            print_line(\@line);
            $line_num2++;
        }
    }

    $idx++;
}

# HTML Footer
if ($html_output) {
    print <<'HTML_FOOT';
</pre></div>
</body>
</html>
HTML_FOOT
}

sub usage {
    my ($exit_code) = @_;
    print <<"USAGE";
Usage: amrdiff.pl [options] <file1> <file2>

Highlight column differences and row differences in tab-delimited files.

Options:
  -C, --context <N>     Number of context lines around differences (default: 2)
  -y, --side-by-side    Display differences side-by-side (like vimdiff)
      --html            Output standalone HTML instead of terminal ANSI
      --no-color        Disable ANSI escape colors (plain text)
  -h, --help            Show this help message

USAGE
    exit $exit_code;
}
