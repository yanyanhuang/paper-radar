from pathlib import Path

import yaml

from journal_fetcher import JOURNAL_CROSSREF_ISSNS, JOURNAL_RSS_FEEDS, JournalFetcher


REQUESTED_JOURNALS = {
    "nature_biotechnology",
    "nature_cancer",
    "npj_digital_medicine",
    "science_translational_medicine",
    "lancet_oncology",
    "nature_neuroscience",
    "neuron",
    "radiology",
    "radiology_artificial_intelligence",
}


def test_config_enables_all_requested_journals():
    config_path = Path(__file__).parents[1] / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sources = {source["key"]: source for source in config["journals"]["sources"]}

    assert REQUESTED_JOURNALS <= sources.keys()
    assert all(sources[key]["enabled"] for key in REQUESTED_JOURNALS)
    assert all(
        key in JOURNAL_RSS_FEEDS or key in JOURNAL_CROSSREF_ISSNS
        for key in REQUESTED_JOURNALS
    )


def test_crossref_item_preserves_metadata_and_builds_rsna_pdf_url():
    fetcher = JournalFetcher({})
    item = {
        "DOI": "10.1148/ryai.250888",
        "title": ["<i>AI</i> for longitudinal mammography"],
        "abstract": "<jats:p>Deep learning &amp; imaging.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "published-online": {"date-parts": [[2026, 7, 29]]},
        "URL": "https://doi.org/10.1148/ryai.250888",
    }

    paper = fetcher._parse_crossref_item(
        item,
        {
            "name": "Radiology: Artificial Intelligence",
            "key": "radiology_artificial_intelligence",
        },
    )

    assert paper is not None
    assert paper.arxiv_id == "radiology_artificial_intelligence:10.1148/ryai.250888"
    assert paper.title == "AI for longitudinal mammography"
    assert paper.summary == "Deep learning & imaging."
    assert paper.authors == ["Ada Lovelace"]
    assert paper.published.isoformat() == "2026-07-29T00:00:00"
    assert paper.pdf_url == "https://pubs.rsna.org/doi/pdf/10.1148/ryai.250888"


def test_crossref_item_extracts_cell_press_pii_for_neuron_pdf():
    fetcher = JournalFetcher({})
    item = {
        "DOI": "10.1016/j.neuron.2026.07.013",
        "title": ["Neural coding"],
        "created": {"date-parts": [[2026, 7, 31]]},
        "resource": {
            "primary": {
                "URL": "https://linkinghub.elsevier.com/retrieve/pii/S0896627326005684"
            }
        },
    }

    paper = fetcher._parse_crossref_item(
        item,
        {"name": "Neuron", "key": "neuron"},
    )

    assert paper is not None
    assert paper.published.isoformat() == "2026-07-31T00:00:00"
    assert paper.summary == ""
    assert paper.pdf_url == "https://www.cell.com/neuron/pdf/S0896627326005684.pdf"
