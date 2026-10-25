# AMRdiff

A diff program for AMRFinderPlus results to highlight column differences and
row differences so they are easier to understand.


## Usage

```bash
./amrdiff.pl [options] <file1> <file2>
```

### Options

- `-C, --context <N>`: Number of context lines around differences (default: `2`). Use `-C -1` to show all lines.
- `-y, --side-by-side`: Display differences side-by-side (like `vimdiff`).
- `--html`: Output standalone HTML instead of terminal ANSI colors.
- `--no-color`: Disable ANSI escape colors.
- `-h, --help`: Show help message.

### Examples

```bash
# Stacked view (default) piped into less
./amrdiff.pl test_data/GCA_006131345.1.amrfinder_old test_data/GCA_006131345.1.amrfinder_new | less -RS

# Side-by-side view
./amrdiff.pl -y test_data/GCA_006131345.1.amrfinder_old test_data/GCA_006131345.1.amrfinder_new | less -RS

# Export HTML diff
./amrdiff.pl --html test_data/GCA_006131345.1.amrfinder_old test_data/GCA_006131345.1.amrfinder_new > diff.html
```
