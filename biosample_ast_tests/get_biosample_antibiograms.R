library(rentrez)
library(xml2)
library(tidyverse)

#' Fetch Antibiograms from NCBI BioSample
#'
#' This function searches for BioSample records matching a genus and the "antibiogram" filter,
#' fetches the XML records, and extracts the structured antibiogram tables into a tidy data frame.
#'
#' @param genus The genus name to search for (e.g., "Klebsiella").
#' @param api_key Optional NCBI API key for faster requests (10 vs 3 per second).
#' @param batch_size Number of records to fetch per batch (default 500). Max is usually 10000 but smaller is safer for large XML.
#' @param output_file Optional file path to save the results as a TSV (e.g., "results.tsv").
#' @return A tibble containing Combined antibiogram data for all found BioSamples, formatted for AMRgen.
#' @export
download_ncbi_ast <- function(genus, api_key = NULL, batch_size = 500, output_file = NULL) {
  
  if (!is.null(api_key)) {
    set_entrez_key(api_key)
  }
  
  # 1. Search for records
  search_term <- paste0(genus, " AND antibiogram[filter]")
  message(paste("Searching for:", search_term))
  
  search_results <- entrez_search(db = "biosample", term = search_term, use_history = TRUE)
  
  if (search_results$count == 0) {
    message("No records found with antibiogram data.")
    return(NULL)
  }
  
  message(paste("Found", search_results$count, "records. Fetching data..."))
  
  # 2. Fetch and parse in batches
  all_antibiograms <- list()
  
  for (start_idx in seq(0, search_results$count - 1, by = batch_size)) {
    # Determine retry info
    message(paste("Fetching records", start_idx + 1, "to", min(start_idx + batch_size, search_results$count)))
    
    # Fetch XML
    recs <- tryCatch({
      entrez_fetch(
        db = "biosample", 
        web_history = search_results$web_history, 
        retstart = start_idx, 
        retmax = batch_size, 
        rettype = "xml", 
        parsed = FALSE
      )
    }, error = function(e) {
      warning(paste("Error fetching batch starting at", start_idx, ":", e$message))
      return(NULL)
    })
    
    if (is.null(recs)) next
    
    # Parse the entire batch XML
    batch_xml <- read_xml(recs)
    
    # Parse each BioSample 
    biosamples <- xml_find_all(batch_xml, "//BioSample")
    
    batch_data <- map_dfr(biosamples, function(bs_node) {
      
      # Extract Accession
      accession <- xml_attr(bs_node, "accession")
      
      # Extract Organism (Scientific name)
      organism <- xml_attr(xml_find_first(bs_node, ".//Organism"), "taxonomy_name")
      
      # Extract BioProject (from Links)
      bioproject <- xml_text(xml_find_first(bs_node, ".//Link[@target='bioproject']"))
      
      # Find Antibiogram Table
      table_node <- xml_find_first(bs_node, ".//Description/Comment/Table[contains(@class, 'Antibiogram')]")
      if (length(table_node) == 0) {
        table_node <- xml_find_first(bs_node, ".//Table[Caption='Antibiogram']")
      }
      
      if (length(table_node) == 0) return(NULL)
      
      # Parse Header
      headers <- xml_text(xml_find_all(table_node, ".//Header/Cell"))
      # Create mapping to target columns manually if needed, or stick to raw and rename
      # XML headers are usually: Antibiotic, Resistance phenotype, Measurement sign, Measurement, Measurement units, etc.
      
      # Parse Rows
      rows <- xml_find_all(table_node, ".//Body/Row")
      if (length(rows) == 0) return(NULL)
      
      # Extract and map
      row_data <- map_dfr(rows, function(row) {
        cells <- xml_text(xml_find_all(row, ".//Cell"))
        # We need to map cells to headers by index since we don't have named cells
        # Create a named list
        row_named <- set_names(as.list(cells), headers)
        as_tibble(row_named)
      })
      
      # Add sample-level metadata
      row_data <- row_data %>%
        mutate(
          BioSample = accession,
          `Scientific name` = organism,
          BioProject = bioproject
        )
      
      return(row_data)
    })
    
    # Post-process batch data to conform to AMRgen expectations
    # We want columns: 
    # BioSample, Antibiotic, Resistance phenotype, Measurement sign, 
    # MIC (mg/L), Disk diffusion (mm), Laboratory typing platform, Testing standard, 
    # Scientific name, BioProject
    
    if (!is.null(batch_data) && nrow(batch_data) > 0) {
      
      # Normalize measurement columns
      # If 'Measurement units' is present, split Measurement into MIC and Disk
      if ("Measurement" %in% names(batch_data) && "Measurement units" %in% names(batch_data)) {
        batch_data <- batch_data %>%
          mutate(
            `MIC (mg/L)` = if_else(`Measurement units` == "mg/L", Measurement, NA_character_),
            `Disk diffusion (mm)` = if_else(`Measurement units` == "mm", Measurement, NA_character_)
          )
      }
      
      # Select and reorder columns to match target format (if present)
      # We allow loose matching, just ensuring key ones are there
      all_antibiograms[[length(all_antibiograms) + 1]] <- batch_data
    }
  }
  
  # 3. Combine all batches
  final_df <- bind_rows(all_antibiograms)
  
  if (!is.null(output_file)) {
    # Write as tab-delimited for compatibility
    write_tsv(final_df, output_file)
    message(paste("Data written to", output_file))
  }
  
  message("Done.")
  return(final_df)
}

# Example Usage (commented out):
# df <- fetch_antibiograms("Klebsiella", batch_size = 50)
# write_csv(df, "klebsiella_antibiograms.csv")
