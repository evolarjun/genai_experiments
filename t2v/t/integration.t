#!/usr/bin/perl
use strict;
use warnings;
use Test::More;
use FindBin qw($Bin);
use POSIX qw(SIGTERM);
use Time::HiRes qw(time sleep);

# ------ Helpers ------------------------------
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
    my $reuse   = $opts{reuse}  // 0;

    $current_cmd    = $cmd;
    $current_width  = $width;
    $current_height = $height;
    @current_keys   = ();

    if ($reuse && system("$tmux has-session -t $SESSION 2>/dev/null") == 0) {
        return $SESSION;
    }

    # Kill any stale session with the same name.
    system($tmux, 'kill-session', '-t', $SESSION) if system("$tmux has-session -t $SESSION 2>/dev/null") == 0;

    system($tmux, 'new-session', '-d', '-s', $SESSION, '-x', $width, '-y', $height, $cmd);
    my $start = Time::HiRes::time();
    while (Time::HiRes::time() - $start < 0.5) {
        my $raw = `$tmux capture-pane -t $SESSION -p 2>/dev/null`;
        last if $raw =~ /\S/;
        Time::HiRes::sleep(0.005);
    }
    return $SESSION;
}

# Capture the pane content and strip ANSI escape sequences.
sub capture {
    my $raw = `$tmux capture-pane -t $SESSION -p 2>/dev/null`;
    $raw =~ s/\e\[[0-9;]*[mABCDHJKfsuhl]//g;  # strip ANSI
    return $raw;
}

# Capture pane content until condition is met or timeout expires.
sub capture_matching {
    my ($pattern, $timeout) = @_;
    $timeout //= 0.3;
    my $start = Time::HiRes::time();
    while (Time::HiRes::time() - $start < $timeout) {
        my $screen = capture();
        if (ref($pattern) eq 'CODE') {
            return $screen if $pattern->($screen);
        } elsif (ref($pattern) eq 'Regexp') {
            return $screen if $screen =~ $pattern;
        } elsif (defined $pattern) {
            return $screen if index($screen, $pattern) >= 0;
        } else {
            return $screen if $screen =~ /\S/;
        }
        Time::HiRes::sleep(0.005);
    }
    return capture();
}

# Send a key sequence to the running pane.
sub send_keys {
    my (@keys) = @_;
    push @current_keys, @keys;
    return unless @keys;
    system($tmux, 'send-keys', '-t', $SESSION, @keys);
    Time::HiRes::sleep(0.005);
}

# Kill the session and print diagnostics if the subtest failed.
sub teardown {
    my (%opts) = @_;
    my $tb = Test::Builder->new;
    if (!$tb->is_passing && $current_cmd) {
        my $msg = "\n  Failed command line:\n    $current_cmd";
        $msg .= "\n  Terminal size: ${current_width}x${current_height}" if $current_width != 80 || $current_height != 24;
        $msg .= "\n  Keys pressed in test: " . join(" -> ", @current_keys) if @current_keys;
        diag($msg . "\n");
    }
    unless ($opts{keep_alive}) {
        system("$tmux kill-session -t $SESSION 2>/dev/null");
    }
}

# -------------------------------------------------------------
# Test Group 1: Default basic.tsv (80x24) - Shared Session
# -------------------------------------------------------------

subtest 'column numbers hidden by default' => sub {
    launch();
    my $screen = capture_matching(qr/name/);
    unlike($screen, qr/1\s+2\s+3/, 'column numbers not visible on initial render');
    teardown(keep_alive => 1);
};

subtest 'header row appears' => sub {
    launch(reuse => 1);
    my $screen = capture();
    like($screen, qr/name/, 'header "name" is visible');
    like($screen, qr/age/,  'header "age" is visible');
    like($screen, qr/score/,'header "score" is visible');
    teardown(keep_alive => 1);
};

subtest 'data rows appear' => sub {
    launch(reuse => 1);
    my $screen = capture();
    like($screen, qr/Alice/, 'first data row visible');
    like($screen, qr/Bob/,   'second data row visible');
    teardown(keep_alive => 1);
};

subtest 'status bar shows filename and row info' => sub {
    launch(reuse => 1);
    my $screen = capture();
    like($screen, qr/t2v\/t\//, 'status bar shows path containing t2v/t/');
    like($screen, qr/Rows\s+\d+-\d+\s+of\s+\d+/, 'status bar shows row range');
    like($screen, qr/Col offset:\s*0/, 'status bar shows horizontal offset = 0');
    teardown(keep_alive => 1);
};

subtest 'help overlay (? / h / dismissal)' => sub {
    launch(reuse => 1);

    # Toggle with ?
    send_keys('?');
    my $screen_q = capture_matching(qr/KEYBINDINGS/);
    like($screen_q, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS on ?');
    like($screen_q, qr/Quit/,        'help overlay shows Quit binding');

    # Dismiss with Escape
    send_keys('Escape');
    capture_matching(sub { $_[0] !~ /KEYBINDINGS/ });

    # Toggle with h
    send_keys('h');
    my $screen_h = capture_matching(qr/KEYBINDINGS/);
    like($screen_h, qr/KEYBINDINGS/, 'help overlay shows KEYBINDINGS on h');
    like($screen_h, qr/Quit/,        'help overlay shows Quit binding');
    like($screen_h, qr/C-b/,         'help overlay shows C-b binding');
    like($screen_h, qr/&/,           'help overlay shows & binding');

    # Dismiss with any key (x)
    send_keys('x');
    my $screen_x = capture_matching(sub { $_[0] !~ /KEYBINDINGS/ });
    my $alive = system("$tmux has-session -t $SESSION 2>/dev/null");
    is($alive, 0, 'session still alive after dismissing help with x');

    teardown(keep_alive => 1);
};

subtest '1 and 0 keys toggle column numbers' => sub {
    launch(reuse => 1);
    my $screen_init = capture();
    unlike($screen_init, qr/1\s+2\s+3/, 'column numbers hidden by default');

    # Press 1 to turn on 1-based column numbers
    send_keys('1');
    my $screen1 = capture_matching(qr/1\s+2\s+3/);
    like($screen1, qr/1\s+2\s+3/, '1-based column numbers shown after pressing 1');

    # Press 1 again to toggle off
    send_keys('1');
    my $screen_off = capture_matching(sub { $_[0] !~ /1\s+2\s+3/ });
    unlike($screen_off, qr/1\s+2\s+3/, 'column numbers hidden after pressing 1 again');

    # Press 0 to turn on 0-based column numbers
    send_keys('0');
    my $screen0 = capture_matching(qr/0\s+1\s+2/);
    like($screen0, qr/0\s+1\s+2/, '0-based column numbers (0 1 2) shown after pressing 0');

    # Press 0 again to toggle 0-based column numbers off
    send_keys('0');
    my $screen_off2 = capture_matching(sub { $_[0] !~ /0\s+1\s+2/ });
    unlike($screen_off2, qr/0\s+1\s+2/, 'column numbers hidden after pressing 0 again');

    # Press 1 to turn back on 1-based column numbers
    send_keys('1');
    my $screen1_back = capture_matching(qr/1\s+2\s+3/);
    like($screen1_back, qr/1\s+2\s+3/, '1-based column numbers restored after pressing 1');

    # Toggle off to leave clean state
    send_keys('1');
    capture_matching(sub { $_[0] !~ /1\s+2\s+3/ });
    teardown(keep_alive => 1);
};

subtest 'search prompt and regex matching' => sub {
    launch(reuse => 1);
    send_keys('/');
    my $prompt = capture_matching(qr/\//);
    like($prompt, qr/\//, 'search prompt starts with /');

    send_keys('A', 'l', 'i', 'c', 'e', 'Enter');
    my $screen = capture_matching(qr/Alice/);
    like($screen, qr/Alice/, 'Alice found and visible on screen');

    teardown(keep_alive => 1);
};

subtest 'search logic --- n and p navigation with wrap around' => sub {
    launch(reuse => 1);

    send_keys('n');
    my $wrap = capture_matching(qr/wrapped/i);
    like($wrap, qr/wrapped/i, 'wrap message shown when wrapping search');

    send_keys('p');
    my $screen = capture_matching(qr/Alice/);
    like($screen, qr/Alice/, 'navigated back to Alice');

    teardown(keep_alive => 1);
};

subtest 'invalid regex handling' => sub {
    launch(reuse => 1);
    send_keys('/', '[', 'Enter');
    my $screen = capture_matching(qr/Invalid regex/i);
    like($screen, qr/Invalid regex/i, 'status bar shows invalid regex error message');

    teardown(keep_alive => 1);
};

subtest '& option filters rows by regex and empty expression clears filter' => sub {
    launch(reuse => 1);

    send_keys('&');
    my $prompt = capture_matching(qr/&/);
    like($prompt, qr/&/, 'filter prompt starts with &');

    send_keys('A', 'l', 'i', 'c', 'e', 'Enter');
    my $filtered = capture_matching(qr/Alice/);
    like($filtered, qr/Alice/, 'Alice is visible when filtered');
    unlike($filtered, qr/Bob/, 'Bob is hidden when filtered');

    send_keys('&', 'Enter');
    my $all = capture_matching(qr/Bob/);
    like($all, qr/Alice/, 'Alice visible after clearing filter');
    like($all, qr/Bob/,   'Bob visible again after clearing filter');

    teardown(keep_alive => 1);
};

subtest 'search history recall with Up arrow' => sub {
    launch(reuse => 1);

    # Run search for Bob
    send_keys('/', 'B', 'o', 'b', 'Enter');
    capture_matching(qr/Bob/);

    # Open search prompt again, press Up arrow
    send_keys('/', 'Up');
    my $prompt = capture_matching(qr/\/Bob/);
    like($prompt, qr/\/Bob/, 'search prompt recalled Bob via Up arrow');

    # Press Enter to execute recalled search
    send_keys('Enter');
    my $screen = capture_matching(qr/Bob/);
    like($screen, qr/Bob/, 'search for Bob executed successfully from history');

    teardown(keep_alive => 1);
};

subtest 'filter history recall and edit with Up arrow' => sub {
    launch(reuse => 1);

    # Run filter for Alice
    send_keys('&', 'A', 'l', 'i', 'c', 'e', 'Enter');
    my $filtered_alice = capture_matching(qr/Alice/);
    like($filtered_alice, qr/Alice/, 'Alice visible');
    unlike($filtered_alice, qr/Bob/, 'Bob hidden');

    # Open filter, press Up to recall Alice, edit to Bob using backspaces
    send_keys('&', 'Up');
    my $prompt_alice = capture_matching(qr/&Alice/);
    like($prompt_alice, qr/&Alice/, 'filter prompt recalled Alice via Up arrow');

    # Backspace 'Alice', type 'Bob', press Enter
    send_keys('BSpace', 'BSpace', 'BSpace', 'BSpace', 'BSpace', 'B', 'o', 'b', 'Enter');
    my $filtered_bob = capture_matching(qr/Bob/);
    like($filtered_bob, qr/Bob/, 'Bob visible after editing recalled filter');
    unlike($filtered_bob, qr/Alice/, 'Alice hidden after editing recalled filter');

    # Open filter again and verify history contains both Bob (newest) and Alice (older)
    send_keys('&', 'Up');
    my $p_bob = capture_matching(qr/&Bob/);
    like($p_bob, qr/&Bob/, 'newest filter history is Bob');

    send_keys('Up');
    my $p_alice = capture_matching(qr/&Alice/);
    like($p_alice, qr/&Alice/, 'previous filter history is Alice');

    # Down arrow restores Bob, then Down arrow restores empty draft
    send_keys('Down');
    my $p_bob_restored = capture_matching(qr/&Bob/);
    like($p_bob_restored, qr/&Bob/, 'Down arrow restored Bob');

    send_keys('Down');
    my $p_draft = capture_matching(qr/&[ ]*$/);
    like($p_draft, qr/&[ ]*$/, 'Down arrow past newest restored empty draft');

    # Cancel prompt and clear active filter
    send_keys('Escape');
    send_keys('&', 'Enter');
    capture_matching(qr/Alice/);
    teardown(keep_alive => 1);
};

subtest 'multi-field search across column boundaries' => sub {
    launch(reuse => 1);

    # Search for pattern spanning name and age: 'Alice\t30'
    send_keys('/', 'A', 'l', 'i', 'c', 'e', 'Tab', '3', '0', 'Enter');
    my $screen = capture_matching(qr/Alice/);
    like($screen, qr/Alice/, 'Alice row found with multi-field search');

    teardown(keep_alive => 1);
};

subtest 'multi-field filter across column boundaries' => sub {
    launch(reuse => 1);

    # Filter by pattern spanning name and age: 'Alice\t30'
    send_keys('&', 'A', 'l', 'i', 'c', 'e', 'Tab', '3', '0', 'Enter');
    my $filtered = capture_matching(qr/Alice/);
    like($filtered, qr/Alice/, 'Alice is visible when multi-field filtered');
    unlike($filtered, qr/Bob/, 'Bob is hidden when multi-field filtered');

    # Clear filter
    send_keys('&', 'Enter');
    capture_matching(qr/Bob/);
    teardown(keep_alive => 1);
};

subtest 'search and match navigation with active filter' => sub {
    launch(reuse => 1);

    # 1. Filter to rows with city containing 'San' (&San)
    send_keys('&', 'S', 'a', 'n', 'Enter');
    my $filtered = capture_matching(qr/Grace/);
    like($filtered, qr/Grace/, 'Grace visible under &San');
    like($filtered, qr/Hank/,  'Hank visible under &San');
    like($filtered, qr/Jack/,  'Jack visible under &San');
    unlike($filtered, qr/Alice/, 'Alice hidden under &San');

    # 2. Search for a pattern present in hidden rows but not visible rows (/Alice)
    send_keys('/', 'A', 'l', 'i', 'c', 'e', 'Enter');
    my $not_found = capture_matching(qr/Pattern not found: Alice/);
    like($not_found, qr/Pattern not found: Alice/, 'searching hidden row content reports pattern not found');

    # 3. Search for a pattern present in visible rows (/San)
    send_keys('/', 'S', 'a', 'n', 'Enter');
    my $s_screen = capture_matching(qr/San Antonio/);
    like($s_screen, qr/San Antonio/, 'first match visible');

    # Navigate next match (n) - should stay within filtered rows
    send_keys('n');
    my $n1 = capture_matching(qr/San Diego/);
    like($n1, qr/San Diego/, 'n navigates to next visible match (Hank / San Diego)');

    send_keys('n');
    my $n2 = capture_matching(qr/San Jose/);
    like($n2, qr/San Jose/, 'n navigates to next visible match (Jack / San Jose)');

    # Next match wraps to top of visible matches
    send_keys('n');
    my $n3 = capture_matching(qr/wrapped to top/i);
    like($n3, qr/wrapped to top/i, 'wrap message when wrapping filtered matches');
    like($n3, qr/San Antonio/, 'wrapped back to Grace / San Antonio');

    # Clear filter
    send_keys('&', 'Enter');
    capture_matching(qr/Alice/);
    teardown(keep_alive => 1);
};

subtest 'w key prompt and column number display' => sub {
    launch(reuse => 1);

    my $initial = capture();
    unlike($initial, qr/1\s+2\s+3/, 'column numbers initially hidden');

    # Press w to open width prompt
    send_keys('w');
    my $prompt = capture_matching(qr/w\s*/);
    like($prompt, qr/w\s*/, 'w prompt active on status bar');
    like($prompt, qr/1\s+2\s+3/, '1-based column numbers temporarily shown during prompt');

    # Cancel prompt with Esc
    send_keys('Escape');
    my $cancelled = capture_matching(sub { $_[0] !~ /w\s*$/ });
    unlike($cancelled, qr/1\s+2\s+3/, 'column numbers restored to hidden after Esc');

    teardown(keep_alive => 1);
};

subtest 'w key shrinks column to header length and toggles back' => sub {
    launch(reuse => 1);

    # Shrink col 1 to header length ('name'=4)
    send_keys('w', '1', 'Enter');
    my $shrunk = capture_matching(qr/Gra>/);
    like($shrunk, qr/Gra>/, 'col 1 truncated to 4 chars');

    # Toggle col 1 back to full width
    send_keys('w', '1', 'Enter');
    my $restored = capture_matching(qr/Grace/);
    like($restored, qr/Grace/, 'col 1 restored to full width');

    teardown(keep_alive => 1);
};

subtest 'w key custom width and hide column' => sub {
    launch(reuse => 1);

    # Hide col 1 with 1:0
    send_keys('w', '1', ':', '0', 'Enter');
    my $hidden = capture_matching(qr/\|\s+age/);
    like($hidden, qr/\|\s+age/, 'col 1 header hidden with |');
    like($hidden, qr/\|\s+30/,  'col 1 data hidden with |');

    # Set col 1 custom width 10
    send_keys('w', '1', ':', '1', '0', 'Enter');
    my $custom = capture_matching(qr/name\s{7}age/);
    like($custom, qr/name\s{7}age/, 'col 1 header expanded with custom width 10');
    like($custom, qr/Alice\s{7}30/, 'col 1 data row expanded with custom width 10');

    # Reset all columns with empty input
    send_keys('w', 'Enter');
    my $reset = capture_matching(qr/name\s{2}age/);
    like($reset, qr/name\s{2}age/, 'col 1 header reset to original width');
    like($reset, qr/Alice\s+30/,    'col 1 data row reset to original width');

    teardown(keep_alive => 1);
};

subtest 'w key hides multiple adjacent columns as ||' => sub {
    launch(reuse => 1);

    # Hide col 2 (age) and col 3 (score)
    send_keys('w', '2', ':', '0', 'Enter');
    capture_matching(qr/name\s+\|\s+score/);
    send_keys('w', '3', ':', '0', 'Enter');
    my $hidden = capture_matching(qr/name\s+\|\|\s+city/);
    like($hidden, qr/name\s+\|\|\s+city/, 'headers show || without spaces between adjacent hidden cols');
    like($hidden, qr/Alice\s+\|\|\s+New York/, 'data rows show || without spaces between adjacent hidden cols');

    # Reset all columns
    send_keys('w', 'Enter');
    capture_matching(qr/name\s+age/);
    teardown(keep_alive => 1);
};

subtest 'w key accepts multiple column specifiers on one line' => sub {
    launch(reuse => 1);

    # Enter multiple specifiers: shrink col 1 (name), hide col 2 (age), set col 3 (score) to 12
    send_keys('w', '1', ' ', '2', ':', '0', ' ', '3', ':', '1', '2', 'Enter');
    my $screen = capture_matching(qr/Gra>/);
    like($screen, qr/Gra>/, 'col 1 shrunk to header length');
    like($screen, qr/\|\s+score/, 'col 2 hidden with |');
    like($screen, qr/\|\s{8}score city/, 'col 3 expanded to width 12');

    # Reset all columns
    send_keys('w', 'Enter');
    capture_matching(qr/name\s+age/);
    teardown(keep_alive => 1);
};

subtest 'pressing Esc after - cancels prompt and returns to normal operation' => sub {
    launch(reuse => 1);
    send_keys('-');
    my $prompt = capture_matching(qr/^\s*-\s*$/m);
    like($prompt, qr/^\s*-\s*$/m, 'dash prompt active on status bar');

    send_keys('Escape');
    my $normal = capture_matching(qr/Rows \d+-\d+ of \d+/);
    like($normal, qr/Rows \d+-\d+ of \d+/, 'returns to normal operation status bar after Esc');
    unlike($normal, qr/^\s*-\s*$/m, 'dash prompt cleared after Esc');
    teardown(keep_alive => 1);
};

subtest 'N key toggles line numbers' => sub {
    launch(reuse => 1);
    my $before = capture();
    unlike($before, qr/^\s*1\s+Alice/m, 'line numbers not shown by default');

    send_keys('N');
    my $prompt = capture_matching(qr/Constantly display line numbers/);
    like($prompt, qr/Constantly display line numbers/, 'prompt message shown after pressing N');
    unlike($prompt, qr/^\s*1\s+Alice/m, 'line numbers not shown until Enter is pressed');

    send_keys('Enter');
    my $after = capture_matching(qr/^\s*1\s+Alice/m);
    like($after, qr/^\s*1\s+Alice/m, 'line numbers shown after pressing Enter');

    send_keys('N');
    my $prompt_off = capture_matching(qr/Don't use line numbers/);
    like($prompt_off, qr/Don't use line numbers/, 'prompt message shown after pressing N when numbers on');

    send_keys('Enter');
    my $off = capture_matching(sub { $_[0] !~ /^\s*1\s+Alice/m });
    unlike($off, qr/^\s*1\s+Alice/m, 'line numbers hidden after pressing Enter');
    teardown(keep_alive => 1);
};

subtest 'q quits the viewer' => sub {
    launch(reuse => 1);
    send_keys('q');
    my $start = Time::HiRes::time();
    my $alive = 0;
    while (Time::HiRes::time() - $start < 0.5) {
        $alive = system("$tmux has-session -t $SESSION 2>/dev/null");
        last if $alive != 0;
        Time::HiRes::sleep(0.005);
    }
    isnt($alive, 0, 'session no longer exists after q');
    teardown();
};

# -------------------------------------------------------------
# Test Group 2: Height 8 Viewport (Vertical Paging & Scrolling)
# -------------------------------------------------------------

subtest 'vertical line navigation (Down/j, Up/k, Home/g, End/G)' => sub {
    launch(height => 8);

    # Down / j scroll down one row
    my $before = capture_matching(qr/Alice/);
    like($before, qr/Alice/, 'Alice visible at top');

    send_keys('Down');
    my $after_down = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_down, qr/\bAlice\b/, 'Alice no longer in top data row after Down');
    like($after_down,   qr/Bob/,       'Bob visible after Down');

    send_keys('Home');
    capture_matching(qr/Alice/);

    send_keys('j');
    my $after_j = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_j, qr/\bAlice\b/, 'Alice scrolled off after j');
    like($after_j,   qr/Bob/,       'Bob visible after j');

    # k scrolls back up one row
    send_keys('k');
    my $after_k = capture_matching(qr/Alice/);
    like($after_k, qr/Alice/, 'Alice visible again after k');

    # Home / g jump to top
    send_keys('Down', 'Down', 'Down');
    my $mid = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($mid, qr/\bAlice\b/, 'Alice not visible mid-scroll');

    send_keys('Home');
    my $top = capture_matching(qr/Alice/);
    like($top, qr/Alice/, 'Alice visible again after Home');

    send_keys('Down', 'Down', 'Down');
    send_keys('g');
    my $top_g = capture_matching(qr/Alice/);
    like($top_g, qr/Alice/, 'Alice visible again after g');

    # End / G jump to last row
    send_keys('End');
    my $last_end = capture_matching(qr/Jack/);
    like($last_end, qr/Jack/, 'Jack (last row) visible after End');

    send_keys('g');
    capture_matching(qr/Alice/);

    send_keys('G');
    my $last_g = capture_matching(qr/Jack/);
    like($last_g, qr/Jack/, 'Jack (last row) visible after G');

    send_keys('Home');
    capture_matching(qr/Alice/);
    teardown(keep_alive => 1);
};

subtest 'vertical paging keybindings and synonyms (PgDn/Space/f/C-f, PgUp/Backspace/b/C-b)' => sub {
    launch(height => 8, reuse => 1);

    # PgDn (NPage)
    send_keys('NPage');
    my $after_pgdn = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_pgdn, qr/\bAlice\b/, 'Alice scrolled off after PgDn');

    send_keys('Home');
    capture_matching(qr/Alice/);

    # Space (PgDn)
    send_keys('Space');
    my $after_space = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_space, qr/\bAlice\b/, 'Alice scrolled off after Space');

    send_keys('Home');
    capture_matching(qr/Alice/);

    # C-f (PgDn)
    send_keys('C-f');
    my $after_cf = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_cf, qr/\bAlice\b/, 'Alice scrolled off after C-f');

    send_keys('Home');
    capture_matching(qr/Alice/);

    # f (PgDn)
    send_keys('f');
    my $after_f = capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    unlike($after_f, qr/\bAlice\b/, 'Alice scrolled off after f');

    # Backspace (PgUp)
    send_keys('Space');
    capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    send_keys('BSpace');
    my $after_bspace = capture_matching(qr/\bAlice\b/);
    like($after_bspace, qr/\bAlice\b/, 'Alice visible again after Backspace');

    # C-b (PgUp)
    send_keys('Space');
    capture_matching(sub { $_[0] !~ /\bAlice\b/ });
    send_keys('C-b');
    my $after_cb = capture_matching(qr/\bAlice\b/);
    like($after_cb, qr/\bAlice\b/, 'Alice visible again after C-b');

    teardown();
};

# -------------------------------------------------------------
# Test Group 3: wide.tsv (Horizontal Scrolling)
# -------------------------------------------------------------

subtest 'horizontal scrolling (Left/Right step, Tab/Shift-Tab viewport)' => sub {
    launch(cmd => "$t2v $fixtures/wide.tsv", width => 80);

    my $before = capture_matching(qr/col1/);
    like($before, qr/col1/, 'col1 header visible before scroll');

    # Right scrolls by step 10
    send_keys('Right');
    my $after_right = capture_matching(qr/Col offset:\s*10/);
    like($after_right, qr/Col offset:\s*10/, 'status bar shows offset 10 after one Right');

    # Left scrolls back to 0
    send_keys('Left');
    my $after_left = capture_matching(qr/Col offset:\s*0/);
    like($after_left, qr/Col offset:\s*0/, 'status bar shows offset 0 after Left');

    # Left at offset 0 stays at 0
    send_keys('Left');
    my $screen_0 = capture_matching(qr/Col offset:\s*0/);
    like($screen_0, qr/Col offset:\s*0/, 'offset stays 0 at left boundary');

    # Tab scrolls 4/5 viewport
    send_keys('Tab');
    my $after_tab = capture_matching(sub { $_[0] !~ /\bcol1\b/ });
    unlike($after_tab, qr/\bcol1\b/, 'col1 scrolled off after Tab');

    # Shift-Tab (BTab) scrolls left 4/5 viewport
    send_keys('BTab');
    my $after_btab = capture_matching(qr/col1/);
    like($after_btab, qr/col1/, 'col1 back after Shift-Tab');

    teardown();
};

# -------------------------------------------------------------
# Test Group 4: Line Numbers Flag (-N)
# -------------------------------------------------------------

subtest '-N flag shows line numbers' => sub {
    launch(cmd => "$t2v -N $fixtures/basic.tsv");
    my $screen = capture_matching(qr/^\s*1\s+Alice/m);
    like($screen, qr/^\s*1\s+Alice/m, 'line number 1 visible next to Alice');
    like($screen, qr/^\s*10\s+Jack/m, 'line number 10 visible next to Jack');
    teardown();
};

subtest '-N flag freezes line numbers column during horizontal scroll' => sub {
    launch(cmd => "$t2v -N $fixtures/wide.tsv", width => 80);
    send_keys('Right');
    my $screen = capture_matching(qr/Col offset:\s*10/);
    like($screen, qr/^\s*1\s+/m, 'line number 1 remains frozen on left after horizontal scroll');
    like($screen, qr/Col offset:\s*10/, 'horizontal scroll offset updated');
    teardown();
};

# -------------------------------------------------------------
# Test Group 5: CLI Flags & Non-TUI Invocations
# -------------------------------------------------------------

subtest '-n flag is invalid when -N is required' => sub {
    my $out = `$t2v -n $fixtures/basic.tsv 2>&1`;
    like($out, qr/Unknown option: n|Usage/i, '-n flag rejected when -N is required');
};

subtest 'CLI usage vs detailed help flags' => sub {
    # Run in tmux where STDIN is a tty
    my $usage_sess = "${SESSION}_usage";
    system($tmux, 'new-session', '-d', '-s', $usage_sess, "sh -c '$t2v 2>&1; sleep 0.5'");
    my $start = Time::HiRes::time();
    my $no_args_out = '';
    while (Time::HiRes::time() - $start < 0.5) {
        $no_args_out = `$tmux capture-pane -t $usage_sess -p 2>/dev/null`;
        last if $no_args_out =~ /Usage:/;
        Time::HiRes::sleep(0.01);
    }
    system($tmux, 'kill-session', '-t', $usage_sess) if system("$tmux has-session -t $usage_sess 2>/dev/null") == 0;

    like($no_args_out, qr/Usage: t2v/, 'no args prints usage');
    unlike($no_args_out, qr/Navigation Commands:/, 'no args does not print detailed command list');

    my $short_h_out = `$t2v -h 2>&1`;
    my $short_h_exit = $? >> 8;
    is($short_h_exit, 0, '-h exits with code 0');
    like($short_h_out, qr/Navigation Commands:/, '-h includes navigation commands');
    like($short_h_out, qr/Search & Filter Commands:/, '-h includes search/filter commands');
    like($short_h_out, qr/Column & Display Commands:/, '-h includes column commands');

    my $long_h_out = `$t2v --help 2>&1`;
    my $long_h_exit = $? >> 8;
    is($long_h_exit, 0, '--help exits with code 0');
    like($long_h_out, qr/Navigation Commands:/, '--help includes navigation commands');
};

# -------------------------------------------------------------
# Test Group 6: Piped Stdin Input
# -------------------------------------------------------------

subtest 'pipe input (cat | t2v)' => sub {
    launch(cmd => "cat $fixtures/basic.tsv | $t2v");
    my $screen = capture_matching(qr/Alice/);
    like($screen, qr/name/,   'header visible from piped input');
    like($screen, qr/Alice/,  'data visible from piped input');
    like($screen, qr/stdin/i, 'status bar shows stdin for piped input');
    teardown();
};

# -------------------------------------------------------------
# Test Group 7: Delimiter & CSV Formats
# -------------------------------------------------------------

subtest 'custom delimiter -d' => sub {
    launch(cmd => "$t2v -d , $fixtures/csv.csv");
    my $screen = capture_matching(qr/Widget/);
    like($screen, qr/product/,  'CSV header "product" visible');
    like($screen, qr/price/,    'CSV header "price" visible');
    like($screen, qr/Widget/,   'CSV data visible');
    teardown(keep_alive => 1);
};

subtest 'long delimiter option --delimiter' => sub {
    launch(cmd => "$t2v --delimiter , $fixtures/csv.csv", reuse => 1);
    my $screen = capture_matching(qr/Widget/);
    like($screen, qr/product/, 'header visible with --delimiter');
    like($screen, qr/Widget/, 'data visible with --delimiter');
    teardown();
};

subtest 'csv option --csv with RFC 4180 parsing' => sub {
    launch(cmd => "$t2v --csv $fixtures/quoted.csv");
    my $screen = capture_matching(qr/Widget/);

    like($screen, qr/name/, 'header visible');
    like($screen, qr/Widget, Basic/, 'embedded comma in quoted string preserved');
    like($screen, qr/Widget "Pro"/, 'escaped quotes unescaped in display');
    like($screen, qr/Multi-line\\ngadget/, 'embedded newline displayed as literal \\n');

    teardown(keep_alive => 1);
};

subtest 'search in CSV mode matches unquoted clean data' => sub {
    launch(cmd => "$t2v --csv $fixtures/quoted.csv", reuse => 1);

    send_keys('/', 'W', 'i', 'd', 'g', 'e', ' ', '"', 'P', 'r', 'o', '"', 'Enter');
    my $screen = capture_matching(qr/Widget "Pro"/);
    like($screen, qr/Widget "Pro"/, 'searched and matched unquoted string');

    teardown();
};

subtest 'csv short option -c' => sub {
    launch(cmd => "$t2v -c $fixtures/quoted.csv");
    my $screen = capture_matching(qr/Widget, Basic/);

    like($screen, qr/Widget, Basic/, 'works with -c flag');
    teardown();
};

done_testing();


