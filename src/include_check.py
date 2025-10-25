def inclusion_decision(extracted):
    txt=" ".join(str(v) for v in extracted.values()).lower()
    tech_ok=any(k in txt for k in["self-sovereign","ssi","did","verifiable credential","keri","acdc"])
    gov_ok=any(k in txt for k in["government","ministry","state","public sector","department","regulator"])
    partner_ok=any(k in txt for k in["partner","consortium","foundation","agency","collaborat"])
    return {
        "tech_ok":tech_ok,
        "gov_ok":gov_ok,
        "partner_ok":partner_ok,
        "eligible":tech_ok and gov_ok and partner_ok
    }
