Fix some issues with FASTA assemblies:

`clean_assemblies.pl` will:
- Remove entries that are all N's
- Trim FASTA entries with N's at the beginning or end
- Remove all entries < 200-nt in length
- Remove duplicates (and revcomp duplicates)

`remove_all_ns.pl`
- Remove entries that are all N's
- Trim FASTA entries with N's at the beginning or end
- Remove all entries < 200-nt in length
