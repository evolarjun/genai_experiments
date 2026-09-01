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

subtest 'column numbers row appears' => sub {
    launch();
    my $screen = capture();
    # Column number row has right-aligned numbers; look for digits separated by spaces
    like($screen, qr/\s1\s/, 'column number 1 visible on screen');
    like($screen, qr/\s2\s/, 'column number 2 visible on screen');
    like($screen, qr/\s3\s/, 'column number 3 visible on screen');
    teardown();
};

subtest 'header row appears' => sub {
    launch();
    my $screen = capture();
    like($screen, qr/name/, 'header "name" is visible');
    like($screen, qr/age/,  'header "age" is visible');
    like($screen, qr/score/,'header "score" is visible');
    teardown();
};

subtest 'data rows appear' => sub {
    launch();
    my $screen = capture();
    like($screen, qr/Alice/, 'first data row visible');
    like($screen, qr/Bob/,   'second data row visible');
    teardown();
};

subtest 'status bar shows filename and row info' => sub {
    launch();
    my $screen = capture();
    like($screen, qr/t2v\/t\//, 'status bar shows path containing t2v/t/');
    like($screen, qr/Rows\s+\d+-\d+\s+of\s+\d+/, 'status bar shows row range');
    like($screen, qr/Col offset:\s*0/, 'status bar shows horizontal offset = 0');
    teardown();
};

subtest 'vertical scroll --- down one row' => sub {
    launch(height => 7);
    my $before = capture();
    like($before, qr/Alice/, 'Alice visible before scroll');

    send_keys('Down');

    my $after = capture();
    unlike($after, qr/\bAlice\b/, 'Alice no longer in top data row after scroll');
    like($after,   qr/Bob/,       'Bob still visible after one scroll');
    teardown();
};

subtest 'vertical scroll --- Home returns to top' => sub {
    launch(height => 7);
    send_keys('Down', 'Down', 'Down');
    my $mid = capture();
    unlike($mid, qr/\bAlice\b/, 'Alice not visible mid-scroll');

    send_keys('Home');
    my $top = capture();
    like($top, qr/Alice/, 'Alice visible again after Home');
    teardown();
};

subtest 'vertical scroll --- End jumps to last row' => sub {
    launch();
    send_keys('End');
    my $screen = capture();
    like($screen, qr/Jack/, 'Jack (last row) visible after End');
    teardown();
};

subtest 'vertical scroll --- g jumps to first row' => sub {
    launch(height => 7);
    send_keys('Down', 'Down', 'Down');
    my $mid = capture();
    unlike($mid, qr/\bAlice\b/, 'Alice not visible mid-scroll');

    send_keys('g');
    my $top = capture();
    like($top, qr/Alice/, 'Alice visible again after g');
    teardown();
};

subtest 'vertical scroll --- G jumps to last row' => sub {
    launch();
    send_keys('G');
    my $screen = capture();
    like($screen, qr/Jack/, 'Jack (last row) visible after G');
    teardown();
};

subtest 'horizontal scroll --- right shifts content' => sub {
    launch(cmd => "$t2v $fixtures/wide.tsv", width => 80);
    my $before = capture();
    like($before, qr/col1/, 'col1 header visible before scroll');

    send_keys('Right');

    my $after = capture();
    like($after, qr/Col offset:\s*10/, 'status bar shows offset 10 after one Right');
    teardown();
};

subtest 'horizontal scroll --- Left at offset 0 stays at 0' => sub {
    launch(cmd => "$t2v $fixtures/wide.tsv", width => 80);
    send_keys('Left');
    my $screen = capture();
    like($screen, qr/Col offset:\s*0/, 'offset stays 0 at left boundary');
    teardown();
};

subtest 'help overlay appears on ?' => sub {
    launch();
    send_keys('?');
    my $screen = capture();
    like($screen, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS');
    like($screen, qr/Quit/,        'help overlay shows Quit binding');
    teardown();
};

subtest 'help overlay appears on h' => sub {
    launch();
    send_keys('h');
    my $screen = capture();
    like($screen, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS');
    like($screen, qr/Quit/,        'help overlay shows Quit binding');
    like($screen, qr/<BS>/,        'help overlay shows <BS> binding');
    like($screen, qr/&/,           'help overlay shows & binding');
    teardown();
};

subtest 'help overlay dismissed by any key' => sub {
    launch();
    send_keys('?');
    send_keys('q');
    select(undef, undef, undef, 0.2);
    my $alive = system("$tmux has-session -t $SESSION 2>/dev/null");
    is($alive, 0, 'session still alive after dismissing help with q');
    teardown();
};

subtest 'q quits the viewer' => sub {
    launch();
    send_keys('q');
    select(undef, undef, undef, 0.3);
    my $alive = system("$tmux has-session -t $SESSION 2>/dev/null");
    isnt($alive, 0, 'session no longer exists after q');
    teardown();
};

subtest 'pipe input (cat | t2v)' => sub {
    launch(cmd => "cat $fixtures/basic.tsv | $t2v");
    my $screen = capture();
    like($screen, qr/name/,   'header visible from piped input');
    like($screen, qr/Alice/,  'data visible from piped input');
    like($screen, qr/stdin/i, 'status bar shows stdin for piped input');
    teardown();
};

subtest 'custom delimiter -d' => sub {
    launch(cmd => "$t2v -d , $fixtures/csv.csv");
    my $screen = capture();
    like($screen, qr/product/,  'CSV header "product" visible');
    like($screen, qr/price/,    'CSV header "price" visible');
    like($screen, qr/Widget/,   'CSV data visible');
    teardown();
};

subtest 'page down scrolls by one screenful' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv", height => 8);

    my $before = capture();
    like($before, qr/Alice/, 'Alice visible before PgDn');

    send_keys('NPage');
    select(undef, undef, undef, 0.2);

    my $after = capture();
    unlike($after, qr/\bAlice\b/, 'Alice scrolled off after PgDn');
    teardown();
};

subtest 'space bar scrolls by one screenful (PgDn synonym)' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv", height => 8);

    my $before = capture();
    like($before, qr/Alice/, 'Alice visible before Space');

    send_keys('Space');
    select(undef, undef, undef, 0.2);

    my $after = capture();
    unlike($after, qr/\bAlice\b/, 'Alice scrolled off after Space');
    teardown();
};

subtest 'backspace key scrolls up by one screenful (PgUp synonym)' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv", height => 8);

    send_keys('Space');
    select(undef, undef, undef, 0.2);
    my $down = capture();
    unlike($down, qr/\bAlice\b/, 'Alice scrolled off after Space');

    send_keys('BSpace');
    select(undef, undef, undef, 0.2);
    my $up = capture();
    like($up, qr/\bAlice\b/, 'Alice visible again after Backspace');
    teardown();
};

subtest '-N flag shows line numbers' => sub {
    launch(cmd => "$t2v -N $fixtures/basic.tsv");
    my $screen = capture();
    like($screen, qr/^\s*1\s+Alice/m, 'line number 1 visible next to Alice');
    like($screen, qr/^\s*10\s+Jack/m, 'line number 10 visible next to Jack');
    teardown();
};

subtest '-n flag is invalid when -N is required' => sub {
    my $out = `$t2v -n $fixtures/basic.tsv 2>&1`;
    like($out, qr/Unknown option: n|Usage/i, '-n flag rejected when -N is required');
};

subtest '-N flag freezes line numbers column during horizontal scroll' => sub {
    launch(cmd => "$t2v -N $fixtures/wide.tsv", width => 80);
    send_keys('Right');
    my $screen = capture();
    like($screen, qr/^\s*1\s+/m, 'line number 1 remains frozen on left after horizontal scroll');
    like($screen, qr/Col offset:\s*10/, 'horizontal scroll offset updated');
    teardown();
};

subtest 'N key toggles line numbers' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");
    my $before = capture();
    unlike($before, qr/^\s*1\s+Alice/m, 'line numbers not shown by default');

    send_keys('N');
    select(undef, undef, undef, 0.2);

    my $prompt = capture();
    like($prompt, qr/Constantly display line numbers/, 'prompt message shown after pressing N');
    unlike($prompt, qr/^\s*1\s+Alice/m, 'line numbers not shown until Enter is pressed');

    send_keys('Enter');
    select(undef, undef, undef, 0.2);

    my $after = capture();
    like($after, qr/^\s*1\s+Alice/m, 'line numbers shown after pressing Enter');

    send_keys('N');
    select(undef, undef, undef, 0.2);

    my $prompt_off = capture();
    like($prompt_off, qr/Don't use line numbers/, 'prompt message shown after pressing N when numbers on');

    send_keys('Enter');
    select(undef, undef, undef, 0.2);

    my $off = capture();
    unlike($off, qr/^\s*1\s+Alice/m, 'line numbers hidden after pressing Enter');
    teardown();
};

subtest 'pressing Esc after - cancels prompt and returns to normal operation' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");
    send_keys('-');
    select(undef, undef, undef, 0.2);

    my $prompt = capture();
    like($prompt, qr/^\s*-\s*$/m, 'dash prompt active on status bar');

    send_keys('Escape');
    select(undef, undef, undef, 0.2);

    my $normal = capture();
    like($normal, qr/Rows \d+-\d+ of \d+/, 'returns to normal operation status bar after Esc');
    unlike($normal, qr/^\s*-\s*$/m, 'dash prompt cleared after Esc');
    teardown();
};

subtest '1 and 0 keys toggle column numbers' => sub {
    launch();
    my $screen1 = capture();
    like($screen1, qr/1\s+2\s+3/, '1-based column numbers shown by default');

    # Press 1 to toggle 1-based column numbers off
    send_keys('1');
    my $screen_off = capture();
    unlike($screen_off, qr/1\s+2\s+3/, 'column numbers hidden after pressing 1');

    # Press 0 to turn on 0-based column numbers
    send_keys('0');
    my $screen0 = capture();
    like($screen0, qr/0\s+1\s+2/, '0-based column numbers (0 1 2) shown after pressing 0');

    # Press 0 again to toggle 0-based column numbers off
    send_keys('0');
    my $screen_off2 = capture();
    unlike($screen_off2, qr/0\s+1\s+2/, 'column numbers hidden after pressing 0 again');

    # Press 1 to turn back on 1-based column numbers
    send_keys('1');
    my $screen1_back = capture();
    like($screen1_back, qr/1\s+2\s+3/, '1-based column numbers restored after pressing 1');
    teardown();
};

subtest 'search prompt and regex matching' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");
    send_keys('/');
    select(undef, undef, undef, 0.2);

    my $prompt = capture();
    like($prompt, qr/\//, 'search prompt starts with /');

    send_keys('A', 'l', 'i', 'c', 'e', 'Enter');
    select(undef, undef, undef, 0.2);

    my $screen = capture();
    like($screen, qr/Alice/, 'Alice found and visible on screen');

    teardown();
};

subtest 'search logic --- n and p navigation with wrap around' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");

    send_keys('/', 'A', 'l', 'i', 'c', 'e', 'Enter');
    select(undef, undef, undef, 0.2);

    send_keys('n');
    select(undef, undef, undef, 0.2);

    my $wrap = capture();
    like($wrap, qr/wrapped/i, 'wrap message shown when wrapping search');

    send_keys('p');
    select(undef, undef, undef, 0.2);

    my $screen = capture();
    like($screen, qr/Alice/, 'navigated back to Alice');

    teardown();
};

subtest 'invalid regex handling' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");
    send_keys('/', '[', 'Enter');
    select(undef, undef, undef, 0.2);

    my $screen = capture();
    like($screen, qr/Invalid regex/i, 'status bar shows invalid regex error message');

    teardown();
};

subtest '& option filters rows by regex and empty expression clears filter' => sub {
    launch(cmd => "$t2v $fixtures/basic.tsv");

    send_keys('&');
    select(undef, undef, undef, 0.2);

    my $prompt = capture();
    like($prompt, qr/&/, 'filter prompt starts with &');

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

    teardown();
};

done_testing();
