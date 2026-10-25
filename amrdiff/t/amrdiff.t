#!/usr/bin/env perl
# t/amrdiff.t - Regression tests for amrdiff.pl

use strict;
use warnings;
use Test::More;
use File::Temp ();
use FindBin qw($Bin);
use File::Spec;

my $SCRIPT = File::Spec->catfile($Bin, '..', 'amrdiff.pl');
ok(-f $SCRIPT, "amrdiff.pl exists at $SCRIPT");
ok(-x $SCRIPT, "amrdiff.pl is executable");

sub run_cmd {
    my (@args) = @_;
    my ($out_fh, $out_fn) = File::Temp::tempfile(UNLINK => 1);
    my ($err_fh, $err_fn) = File::Temp::tempfile(UNLINK => 1);
    close $out_fh;
    close $err_fh;

    my $cmd = join(' ', map { /\s/ ? qq("$_") : $_ } ($^X, $SCRIPT, @args));
    my $rc = system("$cmd > $out_fn 2> $err_fn");
    my $exit_code = $rc >> 8;

    open my $rfh_out, '<', $out_fn or die "Cannot read $out_fn: $!";
    my $stdout = do { local $/; <$rfh_out> } // '';
    close $rfh_out;

    open my $rfh_err, '<', $err_fn or die "Cannot read $err_fn: $!";
    my $stderr = do { local $/; <$rfh_err> } // '';
    close $rfh_err;

    return ($exit_code, $stdout, $stderr);
}

sub write_temp_tsv {
    my ($content) = @_;
    my ($fh, $filename) = File::Temp::tempfile(SUFFIX => '.tsv', UNLINK => 1);
    print $fh $content;
    close $fh;
    return $filename;
}

subtest "CLI and argument validation" => sub {
    my ($rc, $out, $err) = run_cmd();
    isnt($rc, 0, "No arguments exits with error");
    like($err, qr/two input files required/i, "Mentions two input files required");

    ($rc, $out, $err) = run_cmd("dummy.tsv");
    isnt($rc, 0, "Single argument exits with error");

    ($rc, $out, $err) = run_cmd("-h");
    is($rc, 0, "-h exits 0");
    like($out, qr/Usage:\s+amrdiff\.pl/i, "-h shows usage");

    ($rc, $out, $err) = run_cmd("--help");
    is($rc, 0, "--help exits 0");
    like($out, qr/Usage:\s+amrdiff\.pl/i, "--help shows usage");

    ($rc, $out, $err) = run_cmd("non_existent_1.tsv", "non_existent_2.tsv");
    isnt($rc, 0, "Non-existent file exits with error");
    like($err, qr/Cannot open/i, "Mentions cannot open file");
};

subtest "Identical files" => sub {
    my $tsv = "id\tgene\tchrom\tpos\n1\tblaTEM\tchr1\t100\n2\ttetA\tchr1\t200\n";
    my $f1 = write_temp_tsv($tsv);
    my $f2 = write_temp_tsv($tsv);

    my ($rc, $out, $err) = run_cmd($f1, $f2);
    is($rc, 0, "Exit 0 for identical files");
    like($out, qr/id\s+gene\s+chrom\s+pos/, "Header is displayed");
    unlike($out, qr/<|>/, "No diff markers < or > for identical files");
};

subtest "Cell and character level diffs" => sub {
    my $tsv1 = "id\tgene\tchrom\tpos\tqual\n"
             . "v1\tblaTEM-1\tchr1\t10234\t99\n"
             . "v2\ttetA\tchr1\t20500\t95\n";
    my $tsv2 = "id\tgene\tchrom\tpos\tqual\n"
             . "v1\tblaTEM-1\tchr1\t10243\t87\n"
             . "v2\ttetA\tchr1\t20500\t95\n";

    my $f1 = write_temp_tsv($tsv1);
    my $f2 = write_temp_tsv($tsv2);

    my ($rc, $out, $err) = run_cmd($f1, $f2);
    is($rc, 0, "Exit 0 on modified cell");
    like($out, qr/<.*v1.*102.*34.*99/s, "Found < line with old values");
    like($out, qr/>.*v1.*102.*43.*87/s, "Found > line with new values");

    # Verify bold/underline character diff ANSI escapes in changed cell
    like($out, qr/\e\[38;5;203;1;4m34/, "Old changed characters have bold/underline red escape");
    like($out, qr/\e\[38;5;77;1;4m43/, "New changed characters have bold/underline green escape");

    # Verify marker colors
    like($out, qr/\e\[38;5;203m<\s+/, "Old marker < is colored red");
    like($out, qr/\e\[38;5;77m>\s+/, "New marker > is colored green");

    # Verify unchanged cells on the changed line transition to plain text
    like($out, qr/\e\[0mv1\s+blaTEM-1/, "Unchanged cells on modified line are in plain text");
};

subtest "Row insertions and deletions" => sub {
    my $tsv1 = "id\tgene\n"
             . "1\tbla\n"
             . "2\ttet\n"
             . "3\tsul\n";
    my $tsv2 = "id\tgene\n"
             . "1\tbla\n"
             . "3\tsul\n"
             . "4\tqnr\n";

    my $f1 = write_temp_tsv($tsv1);
    my $f2 = write_temp_tsv($tsv2);

    my ($rc, $out, $err) = run_cmd($f1, $f2);
    is($rc, 0, "Exit 0 on add/delete");
    like($out, qr/<\s+2\s+tet/, "Found deleted row with < marker");
    like($out, qr/>\s+4\s+qnr/, "Found added row with > marker");
};

subtest "Context line control (-C)" => sub {
    my $header = "id\tval\n";
    my @rows1 = map { "$_\tv$_\n" } 1 .. 20;
    my @rows2 = @rows1;
    $rows2[9] = "10\tMODIFIED\n"; # Change row 10

    my $f1 = write_temp_tsv($header . join('', @rows1));
    my $f2 = write_temp_tsv($header . join('', @rows2));

    # Test -C 0: only diff rows, no unchanged context lines
    my ($rc0, $out0) = run_cmd("-C", "0", $f1, $f2);
    is($rc0, 0, "-C 0 exits 0");
    like($out0, qr/<.*10.*v10/, "-C 0 has old diff line");
    like($out0, qr/>.*10.*MODIFIED/, "-C 0 has new diff line");
    unlike($out0, qr/\b9\s+v9\b/, "-C 0 does not show row 9 context");
    unlike($out0, qr/\b11\s+v11\b/, "-C 0 does not show row 11 context");
    like($out0, qr/\.\.\. 9 identical lines \.\.\./, "-C 0 folds 9 identical lines before");
    like($out0, qr/\.\.\. 10 identical lines \.\.\./, "-C 0 folds 10 identical lines after");

    # Test -C 2: shows 2 lines before (8, 9) and 2 lines after (11, 12)
    my ($rc2, $out2) = run_cmd("-C", "2", $f1, $f2);
    is($rc2, 0, "-C 2 exits 0");
    like($out2, qr/\b8\s+v8\b/, "-C 2 shows row 8 context");
    like($out2, qr/\b9\s+v9\b/, "-C 2 shows row 9 context");
    like($out2, qr/\b11\s+v11\b/, "-C 2 shows row 11 context");
    like($out2, qr/\b12\s+v12\b/, "-C 2 shows row 12 context");
    unlike($out2, qr/\b7\s+v7\b/, "-C 2 does not show row 7 (folded)");
    unlike($out2, qr/\b13\s+v13\b/, "-C 2 does not show row 13 (folded)");

sub strip_ansi {
    my ($str) = @_;
    $str =~ s/\e\[[0-9;]*m//g;
    return $str;
}

    # Test -C -1: shows all lines without folding
    my ($rc_all, $out_all) = run_cmd("-C", "-1", $f1, $f2);
    is($rc_all, 0, "-C -1 exits 0");
    unlike($out_all, qr/\.\.\. identical lines \.\.\./, "-C -1 has no folded lines");
    my $clean_all = strip_ansi($out_all);
    for my $r (1 .. 20) {
        like($clean_all, qr/\b$r\s+(v$r|MODIFIED)\b/, "-C -1 contains row $r");
    }
};

subtest "ANSI optimization and --no-color" => sub {
    my $header = "id\tcolA\tcolB\n";
    my $r1 = "1\tfoo\tbar\n";
    my $r2 = "2\tbaz\tqux\n";
    my $f1 = write_temp_tsv($header . $r1 . $r2);
    my $f2 = write_temp_tsv($header . $r1 . "2\tbaz\tMODIFIED\n");

    # Test --no-color
    my ($rc_nc, $out_nc) = run_cmd("--no-color", $f1, $f2);
    is($rc_nc, 0, "--no-color exits 0");
    unlike($out_nc, qr/\e\[[0-9;]*m/, "--no-color outputs zero ANSI escape codes");

    # Test ANSI state minimization: context line has exactly 2 escape codes (start dim and EOL reset)
    my ($rc_ansi, $out_ansi) = run_cmd($f1, $f2);
    my @lines = split /\r?\n/, $out_ansi;
    # Line 2 is the unchanged context line for row 1
    my $ctx_line = $lines[1];
    my @escapes = ($ctx_line =~ /\e\[[0-9;]*m/g);
    is(scalar(@escapes), 2, "Unchanged context row emits exactly 2 ANSI codes across all columns");
    like($ctx_line, qr/^\e\[38;5;244m/, "Context row starts with dim code");
    like($ctx_line, qr/\e\[0m$/, "Context row ends with reset code");
};

subtest "Side-by-side view (-y)" => sub {
    my $tsv1 = "id\tgene\n1\tbla\n2\ttet\n";
    my $tsv2 = "id\tgene\n1\tbla\n2\tvan\n";
    my $f1 = write_temp_tsv($tsv1);
    my $f2 = write_temp_tsv($tsv2);

    my ($rc, $out) = run_cmd("-y", $f1, $f2);
    is($rc, 0, "-y exits 0");
    my $clean_y = strip_ansi($out);
    like($clean_y, qr/\s+\|\s+/, "-y output contains vertical divider ' | '");
    like($clean_y, qr/^\s+id\s+gene\s+\|\s+id\s+gene/m, "-y duplicates header on both panes");
    like($clean_y, qr/\b3\s+2\s+tet\s+\|\s+3\s+2\s+van/, "-y shows diff on paired panes");
    like($out, qr/\|\s+\e\[38;5;77m\s*3\s+/, "-y right pane line number is colored green");
};

subtest "HTML output (--html)" => sub {
    my $tsv1 = "id\tgene\n1\tblaTEM\n";
    my $tsv2 = "id\tgene\n1\tblaSHV\n";
    my $f1 = write_temp_tsv($tsv1);
    my $f2 = write_temp_tsv($tsv2);

    my ($rc, $out) = run_cmd("--html", $f1, $f2);
    is($rc, 0, "--html exits 0");
    like($out, qr/<!doctype html>/i, "HTML header present");
    like($out, qr/<div class="diff-container"><pre>/, "Pre container present");
    like($out, qr/<span class="del">/, "Contains span class='del'");
    like($out, qr/<span class="del_ch">TEM<\/span>/, "Contains character diff span class='del_ch'");
    like($out, qr/<span class="add_ch">SHV<\/span>/, "Contains character diff span class='add_ch'");
    like($out, qr/<\/pre><\/div>\s*<\/body>\s*<\/html>/, "HTML footer present");
};

subtest "Smoke tests on real test_data files" => sub {
    my $data_dir = File::Spec->catdir($Bin, '..', 'test_data');
    plan skip_all => "test_data directory not found" unless -d $data_dir;

    # Test set 1: GCA_006131345.1
    my $g1_old = File::Spec->catfile($data_dir, 'GCA_006131345.1.amrfinder_old');
    my $g1_new = File::Spec->catfile($data_dir, 'GCA_006131345.1.amrfinder_new');
    if (-f $g1_old && -f $g1_new) {
        my ($rc, $out) = run_cmd($g1_old, $g1_new);
        is($rc, 0, "GCA_006131345.1 diff exits 0");
        like($out, qr/oqxA/, "Detects oqxA difference in GCA_006131345.1");
        like($out, qr/qacE/, "Detects qacE difference in GCA_006131345.1");

        my ($rc_y, $out_y) = run_cmd("-y", $g1_old, $g1_new);
        is($rc_y, 0, "GCA_006131345.1 side-by-side exits 0");
        like($out_y, qr/ \| /, "GCA_006131345.1 side-by-side has divider");

        my ($rc_h, $out_h) = run_cmd("--html", $g1_old, $g1_new);
        is($rc_h, 0, "GCA_006131345.1 HTML exits 0");
        like($out_h, qr/<!doctype html>/, "GCA_006131345.1 HTML generated");
    }

    # Test set 2: GCA_029070155.1 (deleted row)
    my $g2_old = File::Spec->catfile($data_dir, 'GCA_029070155.1.amrfinder_old');
    my $g2_new = File::Spec->catfile($data_dir, 'GCA_029070155.1.amrfinder_new');
    if (-f $g2_old && -f $g2_new) {
        my ($rc, $out) = run_cmd($g2_old, $g2_new);
        is($rc, 0, "GCA_029070155.1 diff exits 0");
        like($out, qr/<\s+NA.*ere\(A\)/, "Detects deleted row ere(A)");
    }

    # Test set 3: GCA_043566345.1 (added row / changed point mutation)
    my $g3_old = File::Spec->catfile($data_dir, 'GCA_043566345.1.amrfinder_old');
    my $g3_new = File::Spec->catfile($data_dir, 'GCA_043566345.1.amrfinder_new');
    if (-f $g3_old && -f $g3_new) {
        my ($rc, $out) = run_cmd($g3_old, $g3_new);
        is($rc, 0, "GCA_043566345.1 diff exits 0");
        like($out, qr/mexR/, "Detects mexR difference in GCA_043566345.1");
    }
};

done_testing();
