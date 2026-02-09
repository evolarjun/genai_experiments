library(xml2)
library(tidyverse)

#' Convert BioSample XML to Browser TSV Format
#'
#' @param xml_file Path to the input XML file.
#' @param output_file Path to the output TSV file.
#' @return The generated data frame (invisibly).
biosample_xml_to_pd_ast <- function(xml_file, output_file) {
    doc <- read_xml(xml_file)
    biosamples <- xml_find_all(doc, "//BioSample")

    all_data <- map_dfr(biosamples, function(bs) {
        # --- Extract Attributes and Metadata ---
        accession <- xml_attr(bs, "accession")

        # Dates
        # Browser TSV "Create date" seems to not match standard attributes exactly,
        # but publication_date is a good candidate for a date field.
        # User noted dates will be different, so we just pick one availability date.
        create_date <- xml_attr(bs, "publication_date")

        # IDs / Links
        bioproject <- xml_attr(xml_find_first(bs, ".//Link[@target='bioproject']"), "label")
        if (is.na(bioproject)) {
            # Fallback to text content if label is missing
            bioproject <- xml_text(xml_find_first(bs, ".//Link[@target='bioproject']"))
        }
        if (is.na(bioproject)) {
            # Try finding in IDs
            bioproject <- xml_text(xml_find_first(bs, ".//Id[@db='BioProject']"))
        }

        # Organism
        organism_node <- xml_find_first(bs, ".//Organism")
        scientific_name <- xml_attr(organism_node, "taxonomy_name")
        if (is.na(scientific_name)) scientific_name <- xml_text(xml_find_first(organism_node, ".//OrganismName"))

        # Organism Group: In the browser TSV this often duplicates Scientific Name for simple cases
        organism_group <- scientific_name

        # Attributes (Host, Collection date, etc.)
        # We extract all attributes into a named vector first
        # Attributes (Host, Collection date, etc.)
        # We extract all attributes into a named vector first
        attrs <- xml_find_all(bs, ".//Attribute")

        # Prefer harmonized_name, fallback to attribute_name
        attr_names <- xml_attr(attrs, "harmonized_name")
        attr_names[is.na(attr_names)] <- xml_attr(attrs, "attribute_name")[is.na(attr_names)]

        attr_map <- set_names(xml_text(attrs), attr_names)

        # Helpers to safely get attribute or NA
        get_attr <- function(name) {
            val <- attr_map[name]
            if (is.na(val) || tolower(val) == "missing") {
                return("")
            }
            return(val)
        }

        # Explicit mapping based on common BioSample attributes
        host <- get_attr("host")
        collection_date <- get_attr("collection_date")
        isolation_source <- get_attr("isolation_source")
        location <- get_attr("geo_loc_name")
        isolate <- get_attr("strain") # TSV 'Isolate' matches XML 'strain' often, though user said they differ.
        isolation_type <- get_attr("isolation_type") # Usually not present as simple attribute?

        # If isolation_type missing, try to infer from Package
        # User requested NOT to convert/compare Isolation type, so we leave it as is (likely empty).
        # if (is.na(isolation_type) || isolation_type == "") {
        #     package_name <- xml_text(xml_find_first(bs, ".//Package"))
        #     if (!is.na(package_name) && grepl("clinical", package_name, ignore.case = TRUE)) {
        #         isolation_type <- "clinical"
        #     }
        # }

        # --- Extract Antibiogram Table ---
        # Find table
        table_node <- xml_find_first(bs, ".//Table[contains(@class, 'Antibiogram') or Caption='Antibiogram']")

        if (length(table_node) == 0) {
            warning(paste("No antibiogram table found for", accession))
            return(tibble())
        }

        # Headers
        headers_xml <- xml_text(xml_find_all(table_node, ".//Header/Cell"))

        # Rows
        rows <- xml_find_all(table_node, ".//Body/Row")

        ast_data <- map_dfr(rows, function(row) {
            cells <- xml_text(xml_find_all(row, ".//Cell"))
            # Replace "missing" with "" in cells too
            cells[tolower(cells) == "missing"] <- ""
            set_names(as.list(cells), headers_xml) |> as_tibble()
        })

        # --- Transform ANTIBIOGRAM Data to Browser Format ---

        # Browser Format Columns:
        # Antibiotic, Resistance phenotype, Measurement sign, MIC (mg/L), Disk diffusion (mm),
        # Laboratory typing platform, Vendor, Laboratory typing method version or reagent, Testing standard

        # We need to map from XML headers to these columns.
        # XML Headers usually: Antibiotic, Resistance phenotype, Measurement sign, Measurement, Measurement units,
        # Laboratory typing method, Laboratory typing platform, Vendor, ...

        # Helper to format numbers: if it looks like a number, ensure 1 decimal place if integer?
        # The reference has "1.0", "32.0", "8.0", "2.0".
        # But also "0.25", "0.5".
        # So it seems to be standard formatting where integers get .0

        format_mic <- function(x) {
            # Split by slash if needed (though we already removed slashes above? No, we doing it here)
            # We already removed slashes in the if_else below using str_remove

            # Clean first
            x_clean <- str_remove(x, "/.*")

            # Try to convert to numeric
            num <- suppressWarnings(as.numeric(x_clean))

            if (!is.na(num)) {
                # Return formatted.
                # sprintf "%g" might not add .0 for integers.
                # sprintf "%.1f" would parse 0.25 as 0.2? No.
                # Check if integer
                if (num %% 1 == 0) {
                    return(sprintf("%.1f", num))
                } else {
                    return(as.character(num))
                }
            }
            return(x_clean)
        }

        # Handle Measurement splitting
        if ("Measurement" %in% names(ast_data) && "Measurement units" %in% names(ast_data)) {
            ast_data <- ast_data %>%
                rowwise() %>%
                mutate(
                    `MIC (mg/L)` = if_else(`Measurement units` == "mg/L", format_mic(Measurement), ""),
                    `Disk diffusion (mm)` = if_else(`Measurement units` == "mm", format_mic(Measurement), "")
                ) %>%
                ungroup()
        } else {
            if (!"MIC (mg/L)" %in% names(ast_data)) ast_data$`MIC (mg/L)` <- ""
            if (!"Disk diffusion (mm)" %in% names(ast_data)) ast_data$`Disk diffusion (mm)` <- ""
        }

        # Ensure all target columns exist
        target_ast_cols <- c(
            "Antibiotic", "Resistance phenotype", "Measurement sign",
            "MIC (mg/L)", "Disk diffusion (mm)",
            "Laboratory typing platform", "Vendor",
            "Laboratory typing method version or reagent", "Testing standard"
        )

        for (col in target_ast_cols) {
            if (!col %in% names(ast_data)) ast_data[[col]] <- ""
        }

        # Select only target AST columns
        ast_data <- ast_data %>% select(all_of(target_ast_cols))

        # --- Combine Metadata with AST Data ---

        # Add metadata columns to every row
        # Target Meta Columns:
        # #Host, Collection date, BioProject, BioSample, Organism group, Scientific name,
        # Isolation type, Location, Isolation source, Isolate

        # Note: Using #Host with hash to match file header if needed, but usually R handles names.
        # We will rename at the end.

        ast_data %>%
            mutate(
                Host = host,
                `Collection date` = collection_date,
                BioProject = bioproject,
                BioSample = accession,
                `Organism group` = organism_group,
                `Scientific name` = scientific_name,
                `Isolation type` = if (is.na(isolation_type)) "" else isolation_type, # Default generic or empty
                Location = location,
                `Isolation source` = isolation_source,
                Isolate = isolate,
                `Create date` = create_date
            )
    })

    # Reorder columns to match Browser TSV Order
    # Expected Order:
    # Host, Collection date, BioProject, BioSample, Organism group, Scientific name, Isolation type,
    # Location, Isolation source, Isolate,
    # Antibiotic, Resistance phenotype, Measurement sign, MIC (mg/L), Disk diffusion (mm),
    # Laboratory typing platform, Vendor, Laboratory typing method version or reagent, Testing standard,
    # Create date

    final_cols <- c(
        "Host", "Collection date", "BioProject", "BioSample", "Organism group",
        "Scientific name", "Isolation type", "Location", "Isolation source", "Isolate",
        "Antibiotic", "Resistance phenotype", "Measurement sign", "MIC (mg/L)",
        "Disk diffusion (mm)", "Laboratory typing platform", "Vendor",
        "Laboratory typing method version or reagent", "Testing standard", "Create date"
    )

    # Ensure all exist
    for (col in final_cols) {
        if (!col %in% names(all_data)) all_data[[col]] <- ""
    }

    all_data <- all_data %>% select(all_of(final_cols))

    # Rename Host to #Host to match file exactly?
    # The user noted SAMN34257506 has #Isolation source as first column.
    # SAMN04014912 has #Host.
    # It seems the first column gets a # prefix in browser TSV.
    # Since we cannot easily predict which column is first without logic we don't have,
    # we will output standard names and let verification handle the # prefix.

    # all_data <- all_data %>% rename(`#Host` = Host)

    # Write
    write_tsv(all_data, output_file, na = "")

    invisible(all_data)
}
