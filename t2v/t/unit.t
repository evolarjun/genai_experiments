#!/usr/bin/perl
use strict;
use warnings;
use Test::More;
use FindBin qw($Bin);

# ------ Load the t2v script without running _main() ---------------------------------------------------------------------------------------------
# The `unless (caller())` guard in t2v prevents _main() from running when
# the file is loaded via `do`.
my $t2v = "$Bin/../t2v";
do $t2v or die "Could not load $t2v: $@";

# ------ Helpers ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
sub strip_ansi { (my $s = $_[0]) =~ s/\e\[[0-9;]*[mABCDHJKfsuhl]//g; $s }

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# clamp()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'clamp' => sub {
    is(clamp(5,  0, 10), 5,  'within range');
    is(clamp(-1, 0, 10), 0,  'below min clamps to min');
    is(clamp(11, 0, 10), 10, 'above max clamps to max');
    is(clamp(0,  0,  0), 0,  'min == max == val');
    is(clamp(0,  0, 10), 0,  'exactly at min');
    is(clamp(10, 0, 10), 10, 'exactly at max');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# format_cell()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'format_cell --- left-align (text)' => sub {
    is(format_cell('hello', 10, 0), 'hello     ', 'padded right with spaces');
    is(format_cell('hello', 5,  0), 'hello',      'exact fit, no padding');
    is(format_cell('',      4,  0), '    ',        'empty string padded');
    is(format_cell(undef,   4,  0), '    ',        'undef treated as empty');
};

subtest 'format_cell --- right-align (numeric)' => sub {
    is(format_cell('42', 6, 1), '    42', 'right-aligned number');
    is(format_cell('42', 2, 1), '42',     'exact fit, no padding');
    is(format_cell('',   4, 1), '    ',   'empty string padded');
};

subtest 'format_cell --- truncation' => sub {
    is(format_cell('toolong', 5, 0), 'tool>', 'truncated with > for text');
    is(format_cell('toolong', 5, 1), 'tool>', 'truncated with > for numeric');
    is(length(format_cell('toolong', 5, 0)), 5, 'truncated result is exactly width');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# clip_content()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'clip_content' => sub {
    my $content = 'ABCDEFGHIJ';  # 10 chars

    is(clip_content($content, 0, 5),  'ABCDE',      'no offset, clips to width');
    is(clip_content($content, 3, 5),  'DEFGH',      'offset 3, clips to width');
    is(clip_content($content, 0, 15), 'ABCDEFGHIJ     ', 'pads when shorter than width');
    is(clip_content($content, 8, 5),  'IJ   ',      'partial content + padding');
    is(clip_content($content, 20, 5), '     ',      'offset past end --- all spaces');
    is(length(clip_content($content, 0, 7)),  7, 'result is exactly width when longer');
    is(length(clip_content($content, 0, 15)), 15, 'result is exactly width when shorter');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# compute_col_widths()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'compute_col_widths' => sub {
    my $header = ['name', 'age', 'score'];
    my @rows = (
        ['Alice', '30',  '98.5'],
        ['Bob',   '250', '7'],
        ['Carol', '42',  '88.3333'],
    );

    my $widths = compute_col_widths(\@rows, $header, 3, undef);

    is($widths->[0], 5, 'col 0: max(name=4, Alice=5, Bob=3, Carol=5) = 5');
    is($widths->[1], 3, 'col 1: max(age=3, 30=2, 250=3, 42=2) = 3');
    is($widths->[2], 7, 'col 2: max(score=5, 98.5=4, 7=1, 88.3333=7) = 7');
};

subtest 'compute_col_widths --- minimum width from column number' => sub {
    # Column 10 needs at least 2 chars for the column number "10"
    my $header = [map { "c$_" } (1..10)];
    my @rows   = ([map { 'x' } (1..10)]);
    my $widths = compute_col_widths(\@rows, $header, 10, undef);

    is($widths->[0], 2, 'col 1: min width = max(c1=2, x=1, colnum=1) = 2');
    is($widths->[9], 3, 'col 10: min width = max(c10=3, x=1, colnum=2) = 3');
};

subtest 'compute_col_widths --- limit respected' => sub {
    my $header = ['val'];
    my @rows   = (
        ['short'],           # row 0
        ['a_very_long_val'], # row 1 --- should be ignored with limit=1
    );
    my $widths_limited  = compute_col_widths(\@rows, $header, 1, 1);
    my $widths_all      = compute_col_widths(\@rows, $header, 1, undef);

    is($widths_limited->[0], 5, 'limit=1: only sees "short" (5 chars)');
    is($widths_all->[0],    15, 'no limit: sees "a_very_long_val" (15 chars)');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# detect_numeric()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'detect_numeric --- all numeric' => sub {
    my @rows = (['1', '2.5', '-3', '+4', '1e5', '1.2E-3', '0']);
    my $flags = detect_numeric(\@rows, 7, undef);
    ok($flags->[$_], "col $_ is numeric") for 0..6;
};

subtest 'detect_numeric --- text column' => sub {
    my @rows = (['Alice', 'Bob', 'Carol']);
    my $flags = detect_numeric(\@rows, 1, undef);
    ok(!$flags->[0], 'text column is not numeric');
};

subtest 'detect_numeric --- mixed column (one non-numeric --- text)' => sub {
    my @rows = (['1'], ['2'], ['three'], ['4']);
    my $flags = detect_numeric(\@rows, 1, undef);
    ok(!$flags->[0], 'column with any non-numeric value is text');
};

subtest 'detect_numeric --- empty values skipped' => sub {
    my @rows = (['1'], [''], ['3']);
    my $flags = detect_numeric(\@rows, 1, undef);
    ok($flags->[0], 'empty values do not disqualify a numeric column');
};

subtest 'detect_numeric --- limit respected' => sub {
    my @rows = (['1'], ['2'], ['three']);  # "three" at row index 2
    my $flags_limited = detect_numeric(\@rows, 1, 2);   # only sees rows 0+1
    my $flags_all     = detect_numeric(\@rows, 1, undef);

    ok($flags_limited->[0],  'limit=2: only sees numeric rows, column is numeric');
    ok(!$flags_all->[0],     'no limit: sees "three", column is text');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# build_row_line()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'build_row_line' => sub {
    my $fields     = ['Alice', '30', '98.5'];
    my $col_widths = [5, 3, 7];
    my $is_numeric = [0, 1, 1];

    my $line = build_row_line($fields, $col_widths, $is_numeric);
    is($line, 'Alice  30    98.5', 'text left-aligned, numerics right-aligned, joined with space');
    is(length($line), 5 + 1 + 3 + 1 + 7, 'total length = sum of widths + separators');
};

subtest 'build_row_line --- ragged row (missing fields --- empty)' => sub {
    my $fields     = ['Alice'];             # only 1 of 3 fields present
    my $col_widths = [5, 3, 7];
    my $is_numeric = [0, 1, 1];

    my $line = build_row_line($fields, $col_widths, $is_numeric);
    # col 0: 'Alice' left=5, col 1: '' right=3 spaces, col 2: '' right=7 spaces
    # total = 5 + 1 + 3 + 1 + 7 = 17 chars
    is($line, 'Alice            ', 'missing fields render as padded spaces');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# build_colnum_line()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'build_colnum_line' => sub {
    my $col_widths = [5, 3, 7];
    my $is_numeric = [0, 1, 1];
    my $line = build_colnum_line($col_widths, $is_numeric);
    # col 0 (text): "1    ", col 1 (numeric): "  2", col 2 (numeric): "      3"
    is($line, '1       2       3', 'column numbers follow column alignment (text left, numeric right)');
};

subtest 'build_colnum_line --- double-digit numbers' => sub {
    my $col_widths = [(3) x 10];
    my $is_numeric = [(1) x 10];
    my $line = build_colnum_line($col_widths, $is_numeric);
    like($line, qr/^\s*1\s+2\s+.*\s+10$/, 'numbers 1-10 all present');
};

subtest 'build_colnum_line --- 0-based indexing' => sub {
    my $col_widths = [5, 3, 7];
    my $is_numeric = [0, 1, 1];
    my $line = build_colnum_line($col_widths, $is_numeric, 0);
    is($line, '0       1       2', '0-based column numbers start at 0');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# total_line_width()
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'total_line_width' => sub {
    is(total_line_width([5, 3, 7]), 5 + 1 + 3 + 1 + 7, '3 cols: widths + 2 separators');
    is(total_line_width([10]),      10,                  'single col: width only');
    is(total_line_width([]),        0,                   'no cols: 0');
};

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Fixture-based integration of pure functions
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subtest 'fixture: basic.tsv --- column widths and numeric detection' => sub {
    my $fixture = "$Bin/fixtures/basic.tsv";
    open(my $fh, '<', $fixture) or die "Cannot open $fixture: $!";
    my @all_rows;
    while (<$fh>) {
        chomp;
        push @all_rows, [split /\t/, $_, -1];
    }
    close $fh;

    my $header  = $all_rows[0];
    my @data    = @all_rows[1..$#all_rows];
    my $n_cols  = scalar @$header;

    my $widths  = compute_col_widths(\@data, $header, $n_cols, undef);
    my $numeric = detect_numeric(\@data, $n_cols, undef);

    is($widths->[0], 5,  'col 0 (name): max width is 5 (Carol/Frank/Grace)');
    is($widths->[1], 3,  'col 1 (age): header "age" = 3');
    ok(!$numeric->[0],   'col 0 (name) is text');
    ok($numeric->[1],    'col 1 (age) is numeric');
    ok($numeric->[2],    'col 2 (score) is numeric');
    ok(!$numeric->[3],   'col 3 (city) is text');
    ok($numeric->[4],    'col 4 (is_active) is numeric');
};

subtest 'fixture: ragged.tsv --- handles missing fields' => sub {
    my $fixture = "$Bin/fixtures/ragged.tsv";
    open(my $fh, '<', $fixture) or die "Cannot open $fixture: $!";
    my @all_rows;
    while (<$fh>) {
        chomp;
        push @all_rows, [split /\t/, $_, -1];
    }
    close $fh;

    my $header = $all_rows[0];
    my @data   = @all_rows[1..$#all_rows];

    # n_cols is max across all rows
    my $n_cols = scalar @$header;
    for my $r (@data) { $n_cols = scalar(@$r) if scalar(@$r) > $n_cols; }

    my $widths = compute_col_widths(\@data, $header, $n_cols, undef);
    ok($n_cols >= 4, 'ragged file has at least 4 columns (from widest row)');
    ok($widths->[$_] > 0, "col $_ has positive width") for 0..$n_cols-1;

    # Render a short row without dying
    my $short_row = $data[2]; # gamma row has only 2 fields
    my @fields = map { $short_row->[$_] // '' } (0..$n_cols-1);
    my $is_numeric = detect_numeric(\@data, $n_cols, undef);
    my $line = build_row_line(\@fields, $widths, $is_numeric);
    ok(defined $line, 'render_row does not die on ragged row');
    is(length($line), total_line_width($widths), 'rendered line has correct total width');
};

subtest 'format_line_num' => sub {
    is(format_line_num(0, 10),  ' 1 ', 'row 0 of 10 is " 1 "');
    is(format_line_num(9, 10),  '10 ', 'row 9 of 10 is "10 "');
    is(format_line_num(0, 100), '  1 ', 'row 0 of 100 is "  1 "');
    is(format_line_num(undef, 10), '   ', 'undef row is 3 spaces');
};

subtest 'format_option_prompt' => sub {
    my $off_prompt = format_option_prompt(0, 80);
    like($off_prompt, qr/\e\[7;33m/, 'uses reverse yellow ANSI escape code');
    like(strip_ansi($off_prompt), qr/^Constantly display line numbers  \(press RETURN\)\s*$/, 'message when line numbers off');
    is(length(strip_ansi($off_prompt)), 80, 'padded to term_cols width');

    my $on_prompt = format_option_prompt(1, 80);
    like($on_prompt, qr/\e\[7;33m/, 'uses reverse yellow ANSI escape code');
    like(strip_ansi($on_prompt), qr/^Don't use line numbers  \(press RETURN\)\s*$/, 'message when line numbers on');
    is(length(strip_ansi($on_prompt)), 80, 'padded to term_cols width');
};

subtest 'find_matches' => sub {
    my @rows = (
        ['Alice', '30', '98.5'],
        ['Bob',   '250', '7'],
        ['Carol', '42', '88.3333'],
    );
    my $regex = qr/a/i;
    my $matches = find_matches(\@rows, 3, $regex);
    is_deeply($matches, [ [0, 0], [2, 0] ], 'finds case-insensitive matching cells');
};

subtest 'filter_rows' => sub {
    my @rows = (
        ['Alice', '30', 'New York'],
        ['Bob',   '25', 'Los Angeles'],
        ['Carol', '42', 'Chicago'],
    );

    my $all = filter_rows(\@rows, undef);
    is_deeply($all, [0, 1, 2], 'undef regex returns all row indices');

    my $filtered = filter_rows(\@rows, qr/Alice/);
    is_deeply($filtered, [0], 'filters rows matching regex');

    my $city_match = filter_rows(\@rows, qr/Chicago/);
    is_deeply($city_match, [2], 'filters rows matching cell in another column');

    my $nomatch = filter_rows(\@rows, qr/NonExistent/);
    is_deeply($nomatch, [], 'no match returns empty arrayref');
};

subtest 'find_next_match_index --- forward navigation & wrap' => sub {
    my $matches = [ [0, 1], [1, 1], [2, 1] ];

    # From (-1, -1) or start, next is (0, 1) -> index 0
    my ($idx1, $wrap1) = find_next_match_index($matches, -1, -1, 1);
    is($idx1, 0, 'finds first match after start');
    is($wrap1, 0, 'not wrapped');

    # From (0, 1), next is (1, 1) -> index 1
    my ($idx2, $wrap2) = find_next_match_index($matches, 0, 1, 1);
    is($idx2, 1, 'finds next match after (0,1)');
    is($wrap2, 0, 'not wrapped');

    # From (2, 1), next wraps to (0, 1) -> index 0
    my ($idx3, $wrap3) = find_next_match_index($matches, 2, 1, 1);
    is($idx3, 0, 'wraps forward to first match');
    is($wrap3, 1, 'wrapped flag set');
};

subtest 'find_next_match_index --- backward navigation & wrap' => sub {
    my $matches = [ [0, 1], [1, 1], [2, 1] ];

    # From (2, 1), prev is (1, 1) -> index 1
    my ($idx1, $wrap1) = find_next_match_index($matches, 2, 1, -1);
    is($idx1, 1, 'finds previous match before (2,1)');
    is($wrap1, 0, 'not wrapped');

    # From (0, 1), prev wraps to (2, 1) -> index 2
    my ($idx2, $wrap2) = find_next_match_index($matches, 0, 1, -1);
    is($idx2, 2, 'wraps backward to last match');
    is($wrap2, 1, 'wrapped flag set');
};

subtest 'render_clipped_row' => sub {
    my $fields     = ['Alice', '30', '98.5'];
    my $col_widths = [5, 3, 7];
    my $is_numeric = [0, 1, 1];

    # Without search: matches clip_content(build_row_line(...))
    my $plain = render_clipped_row($fields, $col_widths, $is_numeric, 0, 20, 0, undef, undef);
    is($plain, 'Alice  30    98.5   ', 'plain clipped row matches expected spacing');

    # With search: cell 0 matching and active
    my $active = render_clipped_row($fields, $col_widths, $is_numeric, 0, 20, 0, qr/Alice/, [0, 0]);
    like($active, qr/\e\[42;30mAlice\e\[0m/, 'active match highlighted in black on green bg');

    # With search: cell 0 matching but not active
    my $match = render_clipped_row($fields, $col_widths, $is_numeric, 0, 20, 0, qr/Alice/, [1, 0]);
    like($match, qr/\e\[32mAlice\e\[0m/, 'other match highlighted in green text');
};

subtest 'compute_search_h_offset' => sub {
    is(compute_search_h_offset(0, 10, 80, 200), 0, 'entire field visible at offset 0 returns 0');
    is(compute_search_h_offset(5, 10, 80, 200), 0, 'field ending before content_w returns 0 offset');
    is(compute_search_h_offset(100, 20, 80, 200), 70, 'centers field in viewport when not visible at offset 0');
    is(compute_search_h_offset(200, 20, 80, 100), 100, 'clamps target offset to max_h');
};

done_testing();

