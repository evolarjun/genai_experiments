#!/usr/bin/perl
# written by GenAI
# Prompt: Write me a perl script to go through a FASTA file and remove any entries
#         that are all N's, trim leading/trailing N's from remaining contigs,
#         and discard contigs shorter than 200 nt after trimming.
# 
# Further prompts to remove duplicate contigs as well. 

use strict;
use warnings;
use Getopt::Long qw(GetOptions);

my $MIN_LEN = 200;   # minimum contig length after trimming
my %seen_seqs;       # tracks sequences already output, for deduplication
my $quiet = 0;

GetOptions('q' => \$quiet) or die "Usage: perl $0 [-q] <input.fasta> > <output.fasta>\n";

# Check if an input file was provided
my $input_file = $ARGV[0] or die "Remove all-N entries, trim leading/trailing N's, and discard contigs < ${MIN_LEN} nt\n" . 
    "Usage: perl $0 <input.fasta> > <output.fasta>\n";

open(my $fh, '<', $input_file) or die "Cannot open '$input_file': $!\n";

my $header     = '';
my $seq_only   = '';
my $line_width = 80;   # default FASTA line width

# Read the file line by line
while (my $line = <$fh>) {
    if ($line =~ /^>/) {
        # Process the previous sequence record before starting the new one
        process_record($header, $seq_only) if $header;
        
        # Initialize variables for the new record
        $header   = $line;
        $seq_only = '';
    } else {
        # Keep a stripped version (no whitespace/newlines) to test the sequence content
        (my $temp_seq = $line) =~ s/\s+//g;
        # Detect line width from the first full-length sequence line
        $line_width = length($temp_seq) if length($temp_seq) > $line_width;
        $seq_only .= $temp_seq;
    }
}

# Don't forget to process the very last record in the file
process_record($header, $seq_only) if $header;

close($fh);

sub reverse_complement {
    my ($seq) = @_;
    my $revcomp = reverse($seq);
    $revcomp =~ tr/ACGTacgt/TGCAtgca/;
    return $revcomp;
}

sub process_record {
    my ($h, $seq) = @_;
    
    # Strip the contig name from the header for use in messages
    (my $contig_name = $h) =~ s/^>(\S+).*\n?$/$1/;

    # Skip contigs that are entirely N's
    if ($seq =~ /^[Nn]+$/) {
        print STDERR "Removing $contig_name: entirely N's\n" unless $quiet;
        return;
    }

    # Trim leading and trailing N's (case-insensitive)
    my $original_len = length($seq);
    $seq =~ s/^[Nn]+//;
    $seq =~ s/[Nn]+$//;
    if (length($seq) != $original_len) {
        print STDERR "Trimming $contig_name: trimmed from ${original_len} to " . length($seq) . " nt\n" unless $quiet;
    }

    # After trimming, skip if nothing remains or contig is too short
    if (length($seq) < $MIN_LEN) {
        print STDERR "Removing $contig_name: " . length($seq) . " nt after trimming (< $MIN_LEN)\n" unless $quiet;
        return;
    }

    # Skip duplicate sequences (keep first occurrence)
    if (exists $seen_seqs{$seq}) {
        print STDERR "Removing $contig_name: duplicate of $seen_seqs{$seq}\n" unless $quiet;
        return;
    }
    
    # Check for reverse-complement duplicates
    my $revcomp = reverse_complement($seq);
    if (exists $seen_seqs{$revcomp}) {
        print STDERR "Removing $contig_name: reverse-complement duplicate of $seen_seqs{$revcomp}\n" unless $quiet;
        return;
    }
    
    $seen_seqs{$seq} = $contig_name;

    # Re-wrap the trimmed sequence at the line width and print
    print $h;
    while (length($seq) > 0) {
        print substr($seq, 0, $line_width, '') . "\n";
    }
}
