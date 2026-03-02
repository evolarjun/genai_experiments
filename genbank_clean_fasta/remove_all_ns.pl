#!/usr/bin/perl
# written by gemini
# Prompt: Write me a perl script to go through a FASTA file and remove any entries
#         that are all N's, trim leading/trailing N's from remaining contigs,
#         and discard contigs shorter than 200 nt after trimming.

use strict;
use warnings;

my $MIN_LEN = 200;   # minimum contig length after trimming

# Check if an input file was provided
my $input_file = $ARGV[0] or die "Remove all-N entries, trim leading/trailing N's, and discard contigs < ${MIN_LEN} nt\n" . 
    "Usage: perl $0 <input.fasta> > <output.fasta>\n";

open(my $fh, '<', $input_file) or die "Cannot open '$input_file': $!\n";

my $header     = '';
my $seq_block  = '';
my $seq_only   = '';
my $line_width = 80;   # default FASTA line width

# Read the file line by line
while (my $line = <$fh>) {
    if ($line =~ /^>/) {
        # Process the previous sequence record before starting the new one
        process_record($header, $seq_block, $seq_only) if $header;
        
        # Initialize variables for the new record
        $header    = $line;
        $seq_block = '';
        $seq_only  = '';
    } else {
        # Keep the exact formatting for printing
        $seq_block .= $line;
        
        # Keep a stripped version (no whitespace/newlines) to test the sequence content
        my $temp_seq = $line;
        $temp_seq =~ s/\s+//g;
        # Detect line width from the first full-length sequence line
        if (length($temp_seq) > $line_width) {
            $line_width = length($temp_seq);
        }
        $seq_only .= $temp_seq;
    }
}

# Don't forget to process the very last record in the file
process_record($header, $seq_block, $seq_only) if $header;

close($fh);

sub process_record {
    my ($h, $block, $seq) = @_;
    
    # Skip contigs that are entirely N's
    return if $seq =~ /^[Nn]+$/;

    # Trim leading and trailing N's (case-insensitive)
    $seq =~ s/^[Nn]+//;
    $seq =~ s/[Nn]+$//;

    # After trimming, skip if nothing remains or contig is too short
    return if length($seq) < $MIN_LEN;

    # Re-wrap the trimmed sequence at the line width and print
    print $h;
    while (length($seq) > 0) {
        print substr($seq, 0, $line_width, '') . "\n";
    }
}
