-- Reconciliation engine. Paramless by design: DuckDB rejects bind parameters inside
-- CREATE VIEW, so all status/message text stays in Python.
-- Full walkthrough: see DEVELOPER_GUIDE.md § 4.

-- macros: norm / vendor_sim / vendor_threshold / vendor_match / month_match / exact_match
CREATE OR REPLACE MACRO norm(s) AS
    lower(regexp_replace(nfc_normalize(trim(s)), '[\s\x{200B}\x{200C}\x{200D}\x{FEFF}]', '', 'g'));

CREATE OR REPLACE MACRO vendor_sim(a, b) AS COALESCE(jaro_winkler_similarity(norm(a), norm(b)), 0);

CREATE OR REPLACE MACRO vendor_threshold() AS 0.90;

CREATE OR REPLACE MACRO vendor_match(a_eng, b_eng, a_th, b_th) AS
    vendor_sim(a_eng, b_eng) >= vendor_threshold() OR vendor_sim(a_th, b_th) >= vendor_threshold();

CREATE OR REPLACE MACRO month_match(d1, d2) AS
    COALESCE(year(d1) = year(d2) AND month(d1) = month(d2), FALSE);
CREATE OR REPLACE MACRO exact_match(d1, d2) AS COALESCE(d1 = d2, FALSE);

-- scenario_mapping: assigns _er_id, IN_MASTER, and the first-match-wins scenario ladder (0,5,1,2,3,4).
-- EXT_TOTAL_VAT / _doc_first rationale: see DEVELOPER_GUIDE.md § 4.3.
CREATE OR REPLACE VIEW scenario_mapping AS
WITH ext AS (
    SELECT * REPLACE (CAST(INVOICE_NUMBER AS VARCHAR) AS INVOICE_NUMBER)
        , row_number() OVER (ORDER BY FILE_NAME, TAX_INVOICE_NUMBER) AS _er_id
        , (row_number() OVER (
              PARTITION BY FILE_NAME, TAX_INVOICE_NUMBER, BUYER_TAX_ID, VENDOR_TAX_ID, COPY
              ORDER BY (VAT_AMOUNT IS NULL), INVOICE_NUMBER NULLS LAST
          ) = 1) AS _doc_first
    FROM extraction
),
in_master AS (
    SELECT e._er_id
        , COALESCE(BOOL_OR(mv.vendor_code IS NOT NULL), FALSE) AS IN_MASTER
    FROM ext e
    LEFT JOIN master_vendor mv
        ON vendor_match(e.VENDOR_NAME_ENG, mv.vendor_name_eng, e.VENDOR_NAME_TH, mv.vendor_name_th)
    GROUP BY e._er_id
)
SELECT ext.*
    , im.IN_MASTER
    , CASE
        WHEN ext.ISSUE_FLAG IS TRUE OR ext.COPY IS TRUE THEN 0
        WHEN im.IN_MASTER THEN 5
        WHEN ext.VAT_INVOICE IS NOT NULL
             AND ext.INVOICE_NUMBER IS NOT NULL AND trim(ext.INVOICE_NUMBER) <> '' THEN 1
        WHEN ext.VAT_INVOICE IS NOT NULL THEN 2
        WHEN ext.INVOICE_NUMBER IS NOT NULL AND trim(ext.INVOICE_NUMBER) <> '' THEN 3
        ELSE 4
      END AS SCENARIO
    , SUM(ext.VAT_AMOUNT) FILTER (
          WHERE ext._doc_first AND ext.ISSUE_FLAG IS NOT TRUE AND ext.COPY IS NOT TRUE
      ) OVER (
          PARTITION BY ext.TAX_INVOICE_DATE, ext.BUYER_TAX_ID, ext.VENDOR_TAX_ID
      ) AS EXT_TOTAL_VAT
FROM ext
JOIN in_master im USING (_er_id);

-- scen_one: VAT Invoice + Invoice Number. VAT rule: per-line exact. See guide § 4.6.
CREATE OR REPLACE VIEW scen_one AS
WITH base AS (
    SELECT sc.*
        , zz._z_id, zz.ref_doc_inv AS Z_REF_DOC_INV, zz.company AS Z_COMPANY
        , zz.vendor_name AS Z_VENDOR_NAME, zz.vat_amount AS Z_VAT_AMOUNT
        , zz.payment_date AS Z_PAYMENT_DATE, zz.invoice_document AS Z_INVOICE_DOCUMENT
        , zz.vendor_code AS Z_VENDOR_CODE, zz.payment_document AS Z_PAYMENT_DOCUMENT
        , COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE) AS K_COMPANY
        , vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name) AS K_VENDOR
        , COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE) AS K_INVOICE
        , month_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE
        , (K_COMPANY AND K_VENDOR AND K_DATE AND K_INVOICE) AS ALLKEYS
        , (zz._z_id IS NOT NULL) AS HAS_ROW
    FROM scenario_mapping sc
    LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
        OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
    WHERE sc.SCENARIO = 1
),
scored AS (
    SELECT *
        , COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
        , COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
        , (ALLKEYS AND COALESCE(VAT_INVOICE = Z_VAT_AMOUNT, FALSE)) AS CAND_VAT_OK
    FROM base
),
verdict AS (
    SELECT *, COALESCE(BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id), FALSE) AS ER_VAT_OK
    FROM scored
)
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;

-- scen_two: VAT Invoice, no Invoice Number. VAT rule: sum by payment document. See guide § 4.6.
CREATE OR REPLACE VIEW scen_two AS
WITH base AS (
    SELECT sc.*
        , zz._z_id, zz.ref_doc_inv AS Z_REF_DOC_INV, zz.company AS Z_COMPANY
        , zz.vendor_name AS Z_VENDOR_NAME, zz.vat_amount AS Z_VAT_AMOUNT
        , zz.payment_date AS Z_PAYMENT_DATE, zz.invoice_document AS Z_INVOICE_DOCUMENT
        , zz.vendor_code AS Z_VENDOR_CODE, zz.payment_document AS Z_PAYMENT_DOCUMENT
        , COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE) AS K_COMPANY
        , vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name) AS K_VENDOR
        , COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE) AS K_INVOICE
        , month_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE
        , (K_COMPANY AND K_VENDOR AND K_DATE) AS ALLKEYS
        , (zz._z_id IS NOT NULL) AS HAS_ROW
    FROM scenario_mapping sc
    LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
        OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
    WHERE sc.SCENARIO = 2
),
scored AS (
    SELECT *
        , COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
        , COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
        , (ALLKEYS AND COALESCE(VAT_INVOICE = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
              OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT), FALSE)) AS CAND_VAT_OK
    FROM base
),
verdict AS (
    SELECT *, COALESCE(BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id), FALSE) AS ER_VAT_OK
    FROM scored
)
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;

-- scen_three: Invoice Number, no VAT Invoice. VAT rule: header vs document-wide sum. See guide § 4.6.
CREATE OR REPLACE VIEW scen_three AS
WITH base AS (
    SELECT sc.*
        , zz._z_id, zz.ref_doc_inv AS Z_REF_DOC_INV, zz.company AS Z_COMPANY
        , zz.vendor_name AS Z_VENDOR_NAME, zz.vat_amount AS Z_VAT_AMOUNT
        , zz.payment_date AS Z_PAYMENT_DATE, zz.invoice_document AS Z_INVOICE_DOCUMENT
        , zz.vendor_code AS Z_VENDOR_CODE, zz.payment_document AS Z_PAYMENT_DOCUMENT
        , COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE) AS K_COMPANY
        , vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name) AS K_VENDOR
        , COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE) AS K_INVOICE
        , month_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE
        , (K_COMPANY AND K_VENDOR AND K_DATE AND K_INVOICE) AS ALLKEYS
        , (zz._z_id IS NOT NULL) AS HAS_ROW
    FROM scenario_mapping sc
    LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
        OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
    WHERE sc.SCENARIO = 3
),
scored AS (
    SELECT *
        , COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
        , COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
        -- ASSUMPTION: a voucher's line items carry distinct INVOICE_NUMBERs. See guide § 4.6.
        , (ALLKEYS AND COALESCE(VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
              OVER (PARTITION BY FILE_NAME, TAX_INVOICE_NUMBER), FALSE)) AS CAND_VAT_OK
    FROM base
),
verdict AS (
    SELECT *, COALESCE(BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id), FALSE) AS ER_VAT_OK
    FROM scored
)
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;

-- scen_four: neither Invoice Number nor VAT Invoice. VAT rule: sum by payment date + vendor. See guide § 4.6.
CREATE OR REPLACE VIEW scen_four AS
WITH base AS (
    SELECT sc.*
        , zz._z_id, zz.ref_doc_inv AS Z_REF_DOC_INV, zz.company AS Z_COMPANY
        , zz.vendor_name AS Z_VENDOR_NAME, zz.vat_amount AS Z_VAT_AMOUNT
        , zz.payment_date AS Z_PAYMENT_DATE, zz.invoice_document AS Z_INVOICE_DOCUMENT
        , zz.vendor_code AS Z_VENDOR_CODE, zz.payment_document AS Z_PAYMENT_DOCUMENT
        , COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE) AS K_COMPANY
        , vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name) AS K_VENDOR
        , COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE) AS K_INVOICE
        , month_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE
        , (K_COMPANY AND K_VENDOR AND K_DATE) AS ALLKEYS
        , (zz._z_id IS NOT NULL) AS HAS_ROW
    FROM scenario_mapping sc
    LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
        OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
    WHERE sc.SCENARIO = 4
),
scored AS (
    SELECT *
        , COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
        , COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
        , (ALLKEYS AND COALESCE(VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
              OVER (PARTITION BY _er_id, Z_PAYMENT_DATE, Z_VENDOR_NAME), FALSE)) AS CAND_VAT_OK
    FROM base
),
verdict AS (
    SELECT *, COALESCE(BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id), FALSE) AS ER_VAT_OK
    FROM scored
)
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;

-- scen_five: vendor in master (special case). VAT rule: EXT_TOTAL_VAT vs sum by payment document. See guide § 4.6.
CREATE OR REPLACE VIEW scen_five AS
WITH base AS (
    SELECT sc.*
        , zz._z_id, zz.ref_doc_inv AS Z_REF_DOC_INV, zz.company AS Z_COMPANY
        , zz.vendor_name AS Z_VENDOR_NAME, zz.vat_amount AS Z_VAT_AMOUNT
        , zz.payment_date AS Z_PAYMENT_DATE, zz.invoice_document AS Z_INVOICE_DOCUMENT
        , zz.vendor_code AS Z_VENDOR_CODE, zz.payment_document AS Z_PAYMENT_DOCUMENT
        , COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE) AS K_COMPANY
        , vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name) AS K_VENDOR
        , COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE) AS K_INVOICE
        , exact_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE
        , (K_COMPANY AND K_VENDOR AND K_DATE) AS ALLKEYS
        , (zz._z_id IS NOT NULL) AS HAS_ROW
    FROM scenario_mapping sc
    LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
        OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
    WHERE sc.SCENARIO = 5
),
scored AS (
    SELECT *
        , COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
        , COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
        , (ALLKEYS AND COALESCE(EXT_TOTAL_VAT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
              OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT), FALSE)) AS CAND_VAT_OK
    FROM base
),
verdict AS (
    SELECT *, COALESCE(BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id), FALSE) AS ER_VAT_OK
    FROM scored
)
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;
