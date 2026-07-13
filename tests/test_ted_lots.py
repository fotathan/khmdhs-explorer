"""Pure TED eForms/UBL parser tests — parse_notice_xml extracts structured lots
and lot-results, and parse_fulltext stays byte-compatible. No DB required."""
import ted_ingest as ti


def _wrap(inner):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ContractNotice xmlns="urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    xmlns:efac="urn:x" xmlns:efbc="urn:y">
{inner}
</ContractNotice>'''


TWO_LOTS = _wrap('''
  <cac:ProcurementProject>
    <cbc:Name languageID="ELL">Συνολικός διαγωνισμός</cbc:Name>
  </cac:ProcurementProject>
  <cac:ProcurementProjectLot>
    <cbc:ID>LOT-0001</cbc:ID>
    <cac:ProcurementProject>
      <cbc:Name languageID="ELL">Τμήμα Α</cbc:Name>
      <cbc:Description languageID="ELL">Υπολογιστές</cbc:Description>
      <cac:MainCommodityClassification><cbc:ItemClassificationCode>30213100</cbc:ItemClassificationCode></cac:MainCommodityClassification>
      <cac:RealizedLocation><cbc:CountrySubentityCode>EL303</cbc:CountrySubentityCode></cac:RealizedLocation>
      <cbc:EstimatedOverallContractAmount currencyID="EUR">50000</cbc:EstimatedOverallContractAmount>
    </cac:ProcurementProject>
  </cac:ProcurementProjectLot>
  <cac:ProcurementProjectLot>
    <cbc:ID>LOT-0002</cbc:ID>
    <cac:ProcurementProject>
      <cbc:Name languageID="ELL">Τμήμα Β</cbc:Name>
      <cac:MainCommodityClassification><cbc:ItemClassificationCode>30232000</cbc:ItemClassificationCode></cac:MainCommodityClassification>
      <cbc:EstimatedOverallContractAmount currencyID="USD">12000</cbc:EstimatedOverallContractAmount>
    </cac:ProcurementProject>
  </cac:ProcurementProjectLot>''')

ONE_RESULT = _wrap('''
  <cac:ProcurementProjectLot><cbc:ID>LOT-0001</cbc:ID>
    <cac:ProcurementProject><cbc:Name languageID="ELL">Τμήμα Α</cbc:Name></cac:ProcurementProject>
  </cac:ProcurementProjectLot>
  <efac:LotResult>
    <cbc:TenderResultCode>selec-w</cbc:TenderResultCode>
    <cbc:MaximumValueAmount currencyID="EUR">48000</cbc:MaximumValueAmount>
    <efac:TenderLot><cbc:ID>LOT-0001</cbc:ID></efac:TenderLot>
  </efac:LotResult>''')

MULTI_RESULT = _wrap('''
  <cac:ProcurementProjectLot><cbc:ID>LOT-0001</cbc:ID>
    <cac:ProcurementProject><cbc:Name languageID="ELL">A</cbc:Name></cac:ProcurementProject></cac:ProcurementProjectLot>
  <cac:ProcurementProjectLot><cbc:ID>LOT-0002</cbc:ID>
    <cac:ProcurementProject><cbc:Name languageID="ELL">B</cbc:Name></cac:ProcurementProject></cac:ProcurementProjectLot>
  <efac:LotResult><cbc:TenderResultCode>selec-w</cbc:TenderResultCode>
    <efac:TenderLot><cbc:ID>LOT-0001</cbc:ID></efac:TenderLot></efac:LotResult>
  <efac:LotResult><cbc:TenderResultCode>selec-nw</cbc:TenderResultCode>
    <efac:TenderLot><cbc:ID>LOT-0002</cbc:ID></efac:TenderLot></efac:LotResult>''')


def test_two_lots_distinct_fields():
    p = ti.parse_notice_xml(TWO_LOTS)
    lots = {l["lot_identifier"]: l for l in p["lots"]}
    assert set(lots) == {"LOT-0001", "LOT-0002"}
    assert lots["LOT-0001"]["title"] == "Τμήμα Α"
    assert lots["LOT-0001"]["description"] == "Υπολογιστές"
    assert lots["LOT-0001"]["cpvs"] == ["30213100"]
    assert lots["LOT-0001"]["nuts"] == ["EL303"]
    assert lots["LOT-0001"]["estimated_value"] == "50000"
    assert lots["LOT-0001"]["currency"] == "EUR"
    assert lots["LOT-0002"]["currency"] == "USD"
    assert lots["LOT-0002"]["nuts"] == []          # missing NUTS → empty, not error


def test_one_result_references_one_lot():
    p = ti.parse_notice_xml(ONE_RESULT)
    assert len(p["results"]) == 1
    r = p["results"][0]
    assert r["lot_identifier"] == "LOT-0001"
    assert r["result_status"] == "selec-w"
    assert r["maximum_value"] == "48000"
    assert r["currency"] == "EUR"
    assert r["result_ordinal"] == 1


def test_results_reference_multiple_lots():
    p = ti.parse_notice_xml(MULTI_RESULT)
    refs = {r["lot_identifier"] for r in p["results"]}
    assert refs == {"LOT-0001", "LOT-0002"}
    assert [r["result_ordinal"] for r in p["results"]] == [1, 2]


def test_malformed_xml_is_empty():
    p = ti.parse_notice_xml("<not-valid xml <<<")
    assert p == {"summary": None, "full_text": None, "lots": [], "results": []}


def test_missing_optional_fields():
    xml = _wrap('''<cac:ProcurementProjectLot><cbc:ID>LOT-0001</cbc:ID>
        <cac:ProcurementProject><cbc:Name languageID="ELL">Μόνο τίτλος</cbc:Name></cac:ProcurementProject>
      </cac:ProcurementProjectLot>''')
    p = ti.parse_notice_xml(xml)
    lot = p["lots"][0]
    assert lot["estimated_value"] is None and lot["currency"] is None
    assert lot["cpvs"] == [] and lot["nuts"] == []
    assert p["results"] == []


def test_parse_fulltext_compat_tuple():
    # the back-compat wrapper still returns (summary, full_text) and renders lots
    summary, full_text = ti.parse_fulltext(TWO_LOTS)
    assert isinstance(summary, str) and "Συνολικός" in summary
    assert "Τμήμα Α" in full_text and "Τμήμα Β" in full_text
    # and equals render_fulltext(parse_notice_xml(...)) by construction
    assert (summary, full_text) == ti.render_fulltext(ti.parse_notice_xml(TWO_LOTS))
