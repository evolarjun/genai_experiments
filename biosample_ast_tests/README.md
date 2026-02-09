Some tests for getting BioSample AST data
==========================================
This was created while experimeinting with the AMRgen project and code. 



biosample_xml_to_pd_ast.R
-------------------------
The script biosample_xml_to_pd_ast.R converts biosample XML data to AST browser-compatible data. Note that biosample doesn't contain all the fields, so there are a few that 
### "Implementation highlights"
- Header Variation: Supports BioSample files where the first column varies (e.g., Host vs Isolation Source).
- Attributes: Uses harmonized_name to correctly map attributes like collection_date and geo_loc_name even when display names vary.
- BioProject: Extracts the PRJNA... accession from the link label.
- MIC/Disk: Cleans multi-value entries (e.g., 32/16 -> 32.0) and formats integers with .0.
- Missing Values: Correctly maps "missing" or case variants to empty strings.

Note that the loss of information from multi-value entries (32/16) is something I need to look into.

verify_conversion.R
-------------------
Compares the output of biosample_xml_to_pd_ast.R to data downloaded from the AST browser for the same isolates. Three test sets are included:

- SAMN03892126_biosample.xml / SAMN03892126_browser.tsv
- SAMN04014912_biosample.xml / SAMN04014912_browser.tsv
- SAMN34257506_tb_biosample.xml / SAMN34257506_tb_browser.tsv

Note that I have not manually examined these closely. It's possible I will disagree with the conversion if I do. Before using this for actual testing I need to do manual comparisons from XML to tsv.

get_biosample_antibiograms.R
----------------------------
This grabs AST data from BioSample using the eutils API for input to the AMRgen suite.


