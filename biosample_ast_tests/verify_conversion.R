library(tidyverse)
source("biosample_xml_to_pd_ast.R")

# Comparison Settings
exclude_cols <- c("Isolate", "Create date", "Isolation type")
numeric_cols <- c("MIC (mg/L)", "Disk diffusion (mm)")

# Helper function to normalize and compare
compare_files <- function(xml_file, ref_file) {
    message(paste("\n---------------------------------------------------"))
    message(paste("Testing:", xml_file, "vs", ref_file))

    generated_file <- str_replace(xml_file, "_biosample.xml$", "_generated.tsv")

    # Run Conversion
    biosample_xml_to_pd_ast(xml_file, generated_file)

    # Read Files
    ref_data <- read_tsv(ref_file, col_types = cols(.default = "c"), show_col_types = FALSE)
    gen_data <- read_tsv(generated_file, col_types = cols(.default = "c"), show_col_types = FALSE)

    # Normalize
    normalize <- function(df) {
        df %>%
            select(-any_of(exclude_cols)) %>%
            mutate(across(everything(), ~ replace_na(as.character(.), ""))) %>%
            mutate(across(everything(), trimws)) %>%
            rename_with(~ str_remove(., "^#")) %>%
            arrange(Antibiotic, `Measurement sign`, `MIC (mg/L)`, `Disk diffusion (mm)`)
    }

    ref_norm <- normalize(ref_data)
    gen_norm <- normalize(gen_data)

    # Align columns
    common_cols <- intersect(names(ref_norm), names(gen_norm))

    # Check key columns present
    if (length(common_cols) < length(names(ref_norm))) {
        missing <- setdiff(names(ref_norm), names(gen_norm))
        if (length(missing) > 0) message("WARNING: Columns in Ref but missing in Gen:", paste(missing, collapse = ", "))
    }

    ref_norm <- ref_norm %>% select(all_of(common_cols))
    gen_norm <- gen_norm %>% select(all_of(common_cols))

    # Check for differences
    if (nrow(ref_norm) != nrow(gen_norm)) {
        message(paste("FAIL: Row counts differ. Ref:", nrow(ref_norm), "Gen:", nrow(gen_norm)))
        return(FALSE)
    }

    mismatches <- 0

    for (i in seq_len(nrow(ref_norm))) {
        row_match <- TRUE
        diffs <- c()

        for (col in names(ref_norm)) {
            val_ref <- ref_norm[[col]][i]
            val_gen <- gen_norm[[col]][i]

            is_match <- FALSE

            if (val_ref == val_gen) {
                is_match <- TRUE
            } else if (col %in% numeric_cols) {
                # Numeric comparison
                # Remove symbols like >, <, = if they are part of the value (sometimes they are in 'Measurement sign' but checking just in case)
                # Assuming values are clean numbers or "32/16" which we already cleaned to "32.0"

                n_ref <- suppressWarnings(as.numeric(val_ref))
                n_gen <- suppressWarnings(as.numeric(val_gen))

                # Determine equality with tolerance
                if (!is.na(n_ref) && !is.na(n_gen) && abs(n_ref - n_gen) < 1e-9) {
                    is_match <- TRUE
                }
            }

            if (!is_match) {
                row_match <- FALSE
                diffs <- c(diffs, paste0(col, " (Ref: '", val_ref, "' vs Gen: '", val_gen, "')"))
            }
        }

        if (!row_match) {
            mismatches <- mismatches + 1
            # Print first few mismatches only to avoid spam
            if (mismatches <= 10) {
                message(paste("Row", i, "Mismatch:", paste(diffs, collapse = ", ")))
            }
        }
    }

    if (mismatches == 0) {
        message("SUCCESS: Files match.")
        return(TRUE)
    } else {
        message(paste("FAIL:", mismatches, "rows mismatched."))
        return(FALSE)
    }
}

# Test Cases
pairs <- list(
    c("SAMN04014912_biosample.xml", "SAMN04014912_browser.tsv"),
    c("SAMN03892126_biosample.xml", "SAMN03892126_browser.tsv"),
    c("SAMN34257506_tb_biosample.xml", "SAMN34257506_tb_browser.tsv")
)

all_pass <- TRUE
for (pair in pairs) {
    if (!compare_files(pair[1], pair[2])) {
        all_pass <- FALSE
    }
}

if (all_pass) {
    message("\nALL TESTS PASSED")
} else {
    message("\nSOME CHECKS FAILED")
    quit(status = 1)
}
