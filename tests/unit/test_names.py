"""29/10/2026: French words read as tickers (« sur » → Surteco, « aux » → Auxilia…) and real names missed
(« Sartorius Stedim Biotech », « Atos Group »). Names only, normalized; common words never searched alone."""

from app.domain.names import core_name, find_mentions, mention_safe, norm, resolve


def test_core_name_drops_the_legal_form_and_group_words():
    assert core_name("Sartorius Stedim Biotech S.A.") == "sartorius stedim biotech"
    assert core_name("TotalEnergies SE") == "totalenergies"
    assert core_name("Atos Group") == core_name("Atos SE") == "atos"
    assert core_name("Société Générale S.A.") == "societe generale"
    assert norm("L'Oréal") == "l oreal"


def test_only_safe_names_are_searched_in_free_text():
    assert mention_safe("viridien") and mention_safe("sartorius stedim biotech") and mention_safe("atos")
    for word in ("sur", "aux", "pas", "mon", "lien", "data", "next", "orange", "total", "bce"):
        assert not mention_safe(word), word


def test_a_french_letter_finds_the_companies_not_the_words():
    index = {"viridien": "FR001400PVN6", "sartorius stedim biotech": "FR0013154002", "atos": "FR0000051732"}
    text = (
        "Viridien : sortie par le haut ? Le titre a clôturé sur le haut du range. Sartorius Stedim Biotech "
        "reste en tendance haussière. Atos Group : retour sur les plus hauts, pas de signal."
    )
    assert [i for i, _ in find_mentions(text, index)] == ["FR001400PVN6", "FR0013154002", "FR0000051732"]


def test_a_name_given_by_the_reader_is_resolved_on_its_core():
    lookup = {"atos": "FR0000051732", "renault": "FR0000131906", "totalenergies": "FR0000120271"}
    assert resolve("Atos Group", lookup) == "FR0000051732"
    assert resolve("Renault Group", lookup) == "FR0000131906"
    assert resolve("TotalEnergies", lookup) == "FR0000120271"
    assert resolve("Inconnue SA", lookup) is None


def test_a_group_word_is_kept_when_the_rest_is_a_common_word():
    from app.domain.names import name_keys

    lookup = {k: "DE000A1TNV91" for k in name_keys("Bitcoin Group SE")}
    lookup |= {k: "FR0000133308" for k in name_keys("Orange SA")}
    assert resolve("Bitcoin", lookup) is None  # the crypto-currency is not the German company
    assert resolve("Bitcoin Group", lookup) == "DE000A1TNV91"
    assert resolve("Orange", lookup) == "FR0000133308"  # only the legal form dropped
