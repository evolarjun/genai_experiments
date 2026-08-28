#!/usr/bin/env perl
use strict;
use warnings;
use Getopt::Long;
use File::Basename;

sub print_usage {
    my $script = basename($0);
    print STDERR <<"USAGE";
tree_distance.pl - calculates the patristic distance from a taxon on the tree to all
other taxa in the tree.

Usage: perl $script -t TAXON [OPTIONS] [TREE_FILE]

Options:
  -t, --taxon STRING    Taxon identifier to compute distances from (required)
  -i, --tree FILE       Path to Newick tree file (optional if passed as positional argument or STDIN)
  -h, --help            Show this help message

Examples:
  perl $script -t SAMN46712556_gp cluster.9.tree
  perl $script --taxon SAMN46712556_gp --tree cluster.9.tree
  cat cluster.9.tree | perl $script -t SAMN46712556_gp
USAGE
    exit 1;
}

my $taxon_id;
my $tree_file;
my $help;

GetOptions(
    't|taxon=s' => \$taxon_id,
    'i|tree=s'  => \$tree_file,
    'h|help'    => \$help,
) or print_usage();

if ($help) {
    print_usage();
}

unless (defined $taxon_id && length $taxon_id) {
    print STDERR "Error: Missing required option --taxon / -t\n\n";
    print_usage();
}

# If tree file not specified via --tree, check positional argument
if (!defined $tree_file && @ARGV > 0) {
    $tree_file = shift @ARGV;
}

# Read tree content
my $tree_content = "";
if (defined $tree_file && $tree_file ne "-") {
    open my $fh, "<", $tree_file or die "Error: Cannot open tree file '$tree_file': $!\n";
    local $/;
    $tree_content = <$fh>;
    close $fh;
} else {
    # Read from STDIN
    local $/;
    $tree_content = <STDIN>;
}

unless (defined $tree_content && length $tree_content) {
    die "Error: Empty or missing tree input.\n";
}

# Parse Newick tree into graph structure
my ($nodes_ref, $leaves_ref) = parse_newick($tree_content);
my %nodes  = %$nodes_ref;
my %leaves = %$leaves_ref;

# Locate the requested taxon among leaves
unless (exists $leaves{$taxon_id}) {
    die "Error: Taxon '$taxon_id' not found in tree.\n";
}

my $start_node_id = $leaves{$taxon_id};

# Compute shortest distances from start_node_id to all nodes using BFS
my %adj;
for my $id (keys %nodes) {
    my $node = $nodes{$id};
    if (defined $node->{parent}) {
        my $p = $node->{parent};
        my $w = $node->{branch_length} // 0;
        push @{$adj{$id}}, { neighbor => $p, weight => $w };
        push @{$adj{$p}},  { neighbor => $id, weight => $w };
    }
}

my %dist;
my @queue = ($start_node_id);
$dist{$start_node_id} = 0;

while (@queue) {
    my $curr = shift @queue;
    my $d    = $dist{$curr};
    for my $edge (@{$adj{$curr} // []}) {
        my $nxt = $edge->{neighbor};
        my $w   = $edge->{weight};
        if (!exists $dist{$nxt}) {
            $dist{$nxt} = $d + $w;
            push @queue, $nxt;
        }
    }
}

# Collect distances to all other leaf taxa
my @results;
for my $leaf_name (keys %leaves) {
    next if $leaf_name eq $taxon_id; # Exclude self
    my $leaf_node_id = $leaves{$leaf_name};
    my $d = $dist{$leaf_node_id};
    push @results, { taxon => $leaf_name, dist => $d };
}

# Sort by branch length ascending, then taxon name alphabetically
@results = sort {
    $a->{dist} <=> $b->{dist} || $a->{taxon} cmp $b->{taxon}
} @results;

# Output results tab-delimited
for my $r (@results) {
    # Format distance: if integer value, print as integer or standard string without scientific notation artifacts
    my $formatted_dist = ($r->{dist} == int($r->{dist})) ? sprintf("%d", $r->{dist}) : sprintf("%g", $r->{dist});
    print "$r->{taxon}\t$formatted_dist\n";
}

# -----------------------------------------------------------------------------
# Newick Parser Function
# -----------------------------------------------------------------------------
sub parse_newick {
    my ($raw_text) = @_;

    # Strip NHX / comments inside [...]
    $raw_text =~ s/\[[^\]]*\]//g;
    # Strip trailing semicolon and whitespace
    $raw_text =~ s/;\s*$//;
    $raw_text =~ s/^\s+|\s+$//g;

    my $node_id_seq = 0;
    my %nodes;
    my %leaves;

    my $_create_node = sub {
        my $id = ++$node_id_seq;
        $nodes{$id} = {
            id            => $id,
            name          => "",
            branch_length => 0,
            children      => [],
            parent        => undef,
        };
        return $nodes{$id};
    };

    my $_parse_subtree;
    $_parse_subtree = sub {
        my ($str_ref) = @_;
        $$str_ref =~ s/^\s+//;

        my $node = $_create_node->();

        if ($$str_ref =~ s/^\(//) {
            # Internal node
            while (1) {
                my $child = $_parse_subtree->($str_ref);
                $child->{parent} = $node->{id};
                push @{$node->{children}}, $child->{id};

                $$str_ref =~ s/^\s+//;
                if ($$str_ref =~ s/^,//) {
                    next;
                } elsif ($$str_ref =~ s/^\)//) {
                    last;
                } else {
                    die "Parse error: expected ',' or ')' at: " . substr($$str_ref, 0, 30) . "\n";
                }
            }
            # Optional internal node name and branch length
            $$str_ref =~ s/^\s+//;
            if ($$str_ref =~ s/^'([^']*)'// || $$str_ref =~ s/^"([^"]*)"// || $$str_ref =~ s/^([^:,\(\);\s]+)//) {
                $node->{name} = $1;
            }
            $$str_ref =~ s/^\s+//;
            if ($$str_ref =~ s/^:([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)//) {
                $node->{branch_length} = $1 + 0;
            }
        } else {
            # Leaf node
            if ($$str_ref =~ s/^'([^']*)'// || $$str_ref =~ s/^"([^"]*)"// || $$str_ref =~ s/^([^:,\(\);\s]+)//) {
                $node->{name} = $1;
            }
            $$str_ref =~ s/^\s+//;
            if ($$str_ref =~ s/^:([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)//) {
                $node->{branch_length} = $1 + 0;
            }
            if (length $node->{name}) {
                $leaves{$node->{name}} = $node->{id};
            }
        }
        return $node;
    };

    $_parse_subtree->(\$raw_text);

    return (\%nodes, \%leaves);
}
