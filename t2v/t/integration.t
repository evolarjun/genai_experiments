#!/usr/bin/perl
use strict;
use warnings;
use Test::More;
use FindBin qw($Bin);
use POSIX qw(SIGTERM);

# ------ Helpers ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
my $t2v      = "$Bin/../t2v";
my $fixtures = "$Bin/fixtures";
my $SESSION  = "t2v_test_$$";  # unique session name per test run

# Ensure tmux is available.
my $tmux = `which tmux 2>/dev/null`;
chomp $tmux;
plan skip_all => 'tmux not found' unless $tmux && -x $tmux;

# State tracking for failure diagnostics
my $current_cmd    = '';
my $current_width  = 80;
my $current_height = 24;
my @current_keys   = ();

# Launch t2v in a tmux pane of fixed size and return the session name.
# Returns session name (same as $SESSION) on success.
sub launch {
    my (%opts) = @_;
    my $cmd     = $opts{cmd}    // "$t2v $fixtures/basic.tsv";
    my $width   = $opts{width}  // 80;
    my $height  = $opts{height} // 24;

    $current_cmd    = $cmd;
    $current_width  = $width;
    $current_height = $height;
    @current_keys   = ();

    # Kill any stale session with the same name.
    system($tmux, 'kill-session', '-t', $SESSION) if system("$tmux has-session -t $SESSION 2>/dev/null") == 0;

    system($tmux, 'new-session', '-d', '-s', $SESSION, '-x', $width, '-y', $height, $cmd);
    select(undef, undef, undef, 0.4);  # let t2v start up
    return $SESSION;
}

# Capture the pane content and strip ANSI escape sequences.
sub capture {
    my $raw = `$tmux capture-pane -t $SESSION -p 2>/dev/null`;
    $raw =~ s/\e\[[0-9;]*[mABCDHJKfsuhl]//g;  # strip ANSI
    return $raw;
}

# Send a key sequence to the running pane.
sub send_keys {
    my (@keys) = @_;
    push @current_keys, @keys;
    for my $key (@keys) {
        system($tmux, 'send-keys', '-t', $SESSION, $key, '');
        select(undef, undef, undef, 0.1);
    }
}

# Kill the session and print diagnostics if the subtest failed.
sub teardown {
    my $tb = Test::Builder->new;
    if (!$tb->is_passing && $current_cmd) {
        my $msg = "\n  Failed command line:\n    $current_cmd";
        $msg .= "\n  Terminal size: ${current_width}x${current_height}" if $current_width != 80 || $current_height != 24;
        $msg .= "\n  Keys pressed in test: " . join(" -> ", @current_keys) if @current_keys;
        diag($msg . "\n");
    }
    system("$tmux kill-session -t $SESSION 2>/dev/null");
}

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

subtest 'standard interactive workflow' => sub {
    launch();

    # 1. Initial UI rendering
    my $screen = capture();
    like($screen, qr/\s1\s/, 'column number 1 visible on screen');
    like($screen, qr/\s2\s/, 'column number 2 visible on screen');
    like($screen, qr/\s3\s/, 'column number 3 visible on screen');
    like($screen, qr/name/, 'header "name" is visible');
    like($screen, qr/age/,  'header "age" is visible');
    like($screen, qr/score/,'header "score" is visible');
    like($screen, qr/Alice/, 'first data row visible');
    like($screen, qr/Bob/,   'second data row visible');
    like($screen, qr/t2v\/t\//, 'status bar shows path containing t2v/t/');
    like($screen, qr/Rows\s+\d+-\d+\s+of\s+\d+/, 'status bar shows row range');
    like($screen, qr/Col offset:\s*0/, 'status bar shows horizontal offset = 0');

    # 2. 1 and 0 keys toggle column numbers
    send_keys('1');
    my $screen_off = capture();
    unlike($screen_off, qr/1\s+2\s+3/, 'column numbers hidden after pressing 1');

    send_keys('0');
    my $screen0 = capture();
    like($screen0, qr/0\s+1\s+2/, '0-based column numbers (0 1 2) shown after pressing 0');

    send_keys('0');
    my $screen_off2 = capture();
    unlike($screen_off2, qr/0\s+1\s+2/, 'column numbers hidden after pressing 0 again');

    send_keys('1');
    my $screen1_back = capture();
    like($screen1_back, qr/1\s+2\s+3/, '1-based column numbers restored after pressing 1');

    # 3. N key toggles line numbers
    send_keys('N');
    select(undef, undef, undef, 0.2);
    my $prompt_n = capture();
    like($prompt_n, qr/Constantly display line numbers/, 'prompt message shown after pressing N');
    unlike($prompt_n, qr/^\s*1\s+Alice/m, 'line numbers not shown until Enter is pressed');

    send_keys('Enter');
    select(undef, undef, undef, 0.2);
    my $after_n = capture();
    like($after_n, qr/^\s*1\s+Alice/m, 'line numbers shown after pressing Enter');

    send_keys('N');
    select(undef, undef, undef, 0.2);
    my $prompt_off = capture();
    like($prompt_off, qr/Don't use line numbers/, 'prompt message shown after pressing N when numbers on');

    send_keys('Enter');
    select(undef, undef, undef, 0.2);
    my $off_n = capture();
    unlike($off_n, qr/^\s*1\s+Alice/m, 'line numbers hidden after pressing Enter');

    # 4. Pressing Esc after - cancels prompt
    send_keys('-');
    select(undef, undef, undef, 0.2);
    my $prompt_dash = capture();
    like($prompt_dash, qr/^\s*-\s*$/m, 'dash prompt active on status bar');

    send_keys('Escape');
    select(undef, undef, undef, 0.2);
    my $normal_esc = capture();
    like($normal_esc, qr/Rows \d+-\d+ of \d+/, 'returns to normal operation status bar after Esc');
    unlike($normal_esc, qr/^\s*-\s*$/m, 'dash prompt cleared after Esc');

    # 5. Search prompt and regex matching
    send_keys('/');
    select(undef, undef, undef, 0.2);
    my $prompt_srch = capture();
    like($prompt_srch, qr/\//, 'search prompt starts with /');

    send_keys('A', 'l', 'i', 'c', 'e', 'Enter');
    select(undef, undef, undef, 0.2);
    my $screen_srch = capture();
    like($screen_srch, qr/Alice/, 'Alice found and visible on screen');

    # 6. Search logic: n and p navigation with wrap around
    send_keys('n');
    select(undef, undef, undef, 0.2);
    my $wrap = capture();
    like($wrap, qr/wrapped/i, 'wrap message shown when wrapping search');

    send_keys('p');
    select(undef, undef, undef, 0.2);
    my $screen_p = capture();
    like($screen_p, qr/Alice/, 'navigated back to Alice');

    # 7. Invalid regex handling
    send_keys('/', '[', 'Enter');
    select(undef, undef, undef, 0.2);
    my $screen_inv = capture();
    like($screen_inv, qr/Invalid regex/i, 'status bar shows invalid regex error message');

    # 8. Filter option & and clear filter
    send_keys('&');
    select(undef, undef, undef, 0.2);
    my $prompt_fltr = capture();
    like($prompt_fltr, qr/&/, 'filter prompt starts with &');

    send_keys('A', 'l', 'i', 'c', 'e', 'Enter');
    select(undef, undef, undef, 0.2);
    my $filtered = capture();
    like($filtered, qr/Alice/, 'Alice is visible when filtered');
    unlike($filtered, qr/Bob/, 'Bob is hidden when filtered');

    send_keys('&', 'Enter');
    select(undef, undef, undef, 0.2);
    my $all = capture();
    like($all, qr/Alice/, 'Alice visible after clearing filter');
    like($all, qr/Bob/,   'Bob visible again after clearing filter');

    # 9. Help overlay
    send_keys('?');
    my $screen_help1 = capture();
    like($screen_help1, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS');
    like($screen_help1, qr/Quit/,        'help overlay shows Quit binding');

    send_keys('q');
    select(undef, undef, undef, 0.2);
    my $alive_help = system("$tmux has-session -t $SESSION 2>/dev/null");
    is($alive_help, 0, 'session still alive after dismissing help with q');

    send_keys('h');
    my $screen_help2 = capture();
    like($screen_help2, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS');
    like($screen_help2, qr/Quit/,        'help overlay shows Quit binding');
    like($screen_help2, qr/<BS>/,        'help overlay shows <BS> binding');
    like($screen_help2, qr/&/,           'help overlay shows & binding');

    send_keys('Escape');
    select(undef, undef, undef, 0.2);

    # 10. Vertical navigation (End, Home, G, g)
    send_keys('End');
    my $screen_end = capture();
    like($screen_end, qr/Jack/, 'Jack (last row) visible after End');

    send_keys('Home');
    my $screen_home = capture();
    like($screen_home, qr/Alice/, 'Alice visible again after Home');

    send_keys('G');
    my $screen_G = capture();
    like($screen_G, qr/Jack/, 'Jack (last row) visible after G');

    send_keys('g');
    my $screen_g = capture();
    like($screen_g, qr/Alice/, 'Alice visible again after g');

    # 11. Quit viewer
    send_keys('q');
    select(undef, undef, undef, 0.3);
    my $alive_q = system("$tmux has-session -t $SESSION 2>/dev/null");
    isnt($alive_q, 0, 'session no longer exists after q');

    teardown();
};

subtest 'small height vertical scrolling' => sub {
    launch(height => 8);

    my $before = capture();
    like($before, qr/Alice/, 'Alice visible before scroll');

    send_keys('Down');
    my $after_down = capture();
    unlike($after_down, qr/\bAlice\b/, 'Alice no longer in top data row after scroll');
    like($after_down,   qr/Bob/,       'Bob still visible after one scroll');

    send_keys('Home');
    my $top = capture();
    like($top, qr/Alice/, 'Alice visible again after Home');

    send_keys('Down', 'Down', 'Down');
    my $mid = capture();
    unlike($mid, qr/\bAlice\b/, 'Alice not visible mid-scroll');

    send_keys('g');
    my $top_g = capture();
    like($top_g, qr/Alice/, 'Alice visible again after g');

    # Page down
    send_keys('g');
    send_keys('NPage');
    select(undef, undef, undef, 0.2);
    my $after_pgdn = capture();
    unlike($after_pgdn, qr/\bAlice\b/, 'Alice scrolled off after PgDn');

    # Space bar
    send_keys('g');
    send_keys('Space');
    select(undef, undef, undef, 0.2);
    my $after_space = capture();
    unlike($after_space, qr/\bAlice\b/, 'Alice scrolled off after Space');

    # Backspace (PgUp synonym)
    send_keys('BSpace');
    select(undef, undef, undef, 0.2);
    my $after_bspace = capture();
    like($after_bspace, qr/\bAlice\b/, 'Alice visible again after Backspace');

    teardown();
};

subtest 'horizontal scrolling and line numbers freeze' => sub {
    launch(cmd => "$t2v $fixtures/wide.tsv", width => 80);
    my $before = capture();
    like($before, qr/col1/, 'col1 header visible before scroll');

    send_keys('Right');
    my $after_r = capture();
    like($after_r, qr/Col offset:\s*10/, 'status bar shows offset 10 after one Right');

    send_keys('Left');
    send_keys('Left');
    my $after_l = capture();
    like($after_l, qr/Col offset:\s*0/, 'offset stays 0 at left boundary');
    teardown();

    launch(cmd => "$t2v -N $fixtures/wide.tsv", width => 80);
    send_keys('Right');
    my $screen_freeze = capture();
    like($screen_freeze, qr/^\s*1\s+/m, 'line number 1 remains frozen on left after horizontal scroll');
    like($screen_freeze, qr/Col offset:\s*10/, 'horizontal scroll offset updated');
    teardown();
};

subtest 'CLI options and flags' => sub {
    launch(cmd => "$t2v -N $fixtures/basic.tsv");
    my $screen_n = capture();
    like($screen_n, qr/^\s*1\s+Alice/m, 'line number 1 visible next to Alice');
    like($screen_n, qr/^\s*10\s+Jack/m, 'line number 10 visible next to Jack');
    teardown();

    my $out = `$t2v -n $fixtures/basic.tsv 2>&1`;
    like($out, qr/Unknown option: n|Usage/i, '-n flag rejected when -N is required');

    launch(cmd => "$t2v -d , $fixtures/csv.csv");
    my $screen_csv = capture();
    like($screen_csv, qr/product/,  'CSV header "product" visible');
    like($screen_csv, qr/price/,    'CSV header "price" visible');
    like($screen_csv, qr/Widget/,   'CSV data visible');
    teardown();
};

subtest 'piped input' => sub {
    launch(cmd => "cat $fixtures/basic.tsv | $t2v");
    my $screen = capture();
    like($screen, qr/name/,   'header visible from piped input');
    like($screen, qr/Alice/,  'data visible from piped input');
    like($screen, qr/stdin/i, 'status bar shows stdin for piped input');
    teardown();
};

done_testing();
