"""Conservative employer attribution; a generic prior-role verb is insufficient."""
import re
from .sources import normalized


def aliases(company):
    values = {company.casefold(), re.sub(r",?\s+(?:Inc\.?|Corp\.?|Corporation|LLC)$", "", company, flags=re.I).casefold()}
    first = company.split()[0]
    if first.isupper() and len(first) > 1:
        values.add(first.casefold())
    return values | {"the company", "company"}


def company_link(text, company):
    text = normalized(text).casefold()
    return any(re.search(r"\b" + re.escape(a) + r"\b", text) for a in aliases(company))


def internal_employer(role, profile, company):
    """Establish employer from role-local text or dated, explicit biography scope.

    Shared-date internal offices can establish a concurrent role's company scope.
    A historical outside recruitment never classifies a later promotion external.
    """
    q = normalized(role['quote'])
    pattern = r"\b" + r"\W+".join(re.escape(w) for w in re.findall(r"\w+", role['position'])) + r"\b"
    matches = list(re.finditer(pattern, q, re.I))
    from .management import prior_role_date_pairs
    boundaries = [0] + [m.end() for m in re.finditer(r"(?<![A-Z])(?<!Mr)(?<!Dr)(?<!Ms)\.\s+(?=[A-Z])", q)] + [len(q)]
    candidates = []
    for match in matches:
        start = max(x for x in boundaries if x <= match.start())
        end = min(x for x in boundaries if x >= match.end())
        pairs = prior_role_date_pairs(role['position'], q[start:end])
        if not pairs or (role['start_date'], role['end_date']) in pairs:
            candidates.append(match)
    if len(candidates) > 1:
        raise ValueError("Ambiguous repeated prior-role employer attribution at the disclosed dates")
    found = candidates[0] if candidates else None
    if not found:
        raise ValueError("Prior role employer/date evidence is not bound to the disclosed position")
    local = q[found.start():found.end()] + re.split(r"[.;]|\bfrom\b|\bsince\b", q[found.end():], maxsplit=1)[0]
    # Never let selected-company mentions elsewhere override explicit employer attribution.
    suffix = re.search(r"\b(?:of|at|for|with)\s+(.+?)(?=\s+(?:from|since|through|effective)\b|[.;]|$)", local, re.I)
    joined_role = re.search(r"\bjoined\s+(.+?)\s+(?:in|as)\s+(.+)", q, re.I)
    joined_end = (joined_role.start() + len(re.split(r"(?<!St)\.\s+(?=[A-Z])", q[joined_role.start():], maxsplit=1)[0])) if joined_role else 0
    established_entity = bool(joined_role and company_link(joined_role[1], company)
                              and joined_role.start() <= found.start() < joined_end
                              and not re.search(r"\b(?:prior to|previously|before joining)\b", q[joined_role.start():found.start()], re.I))
    if suffix:
        employer = suffix[1].strip()
        if company_link(employer, company):
            return "explicit_selected_company"
        if not established_entity and not re.search(r"^the .+\b(?:Group|Division|Region|Services)\b", employer, re.I):
            raise ValueError("Prior role explicitly belongs to an outside/unestablished employer: " + employer)
    before = q[max(0, found.start()-140):found.start()]
    if re.search(r"(?:the Company|" + re.escape(company) + r")[’']s\s*$", before, re.I):
        return "explicit_company_possessive"
    entity_names = re.findall(r"[—–-]\s*([A-Z][A-Za-z ]{1,60}?(?:Group|Division|Region))\s+of the Company", q)
    compact_role = re.sub(r"\W+", "", role['position']).casefold()
    if any(re.sub(r"\W+", "", e).casefold() in compact_role for e in entity_names):
        return "explicit_internal_entity"

    outside = re.search(r"served\s+(?!as\b|in\b)(.+?)\s+as\s*$", before, re.I)
    if outside and not company_link(outside[1], company):
        raise ValueError("Prior role explicitly attributed to another employer: " + outside[1])
    if company_link(q[found.start():found.end()], company):
        return "explicit_selected_company"
    bio = normalized(profile['quote'])
    joined = re.search(r"\bjoined\s+(.+?)\s+(?:in|as)\s+(.+?)(?=\.|$)", bio, re.I)
    if joined and company_link(joined[1], company):
        years = re.findall(r"\b(?:19|20)\d{2}\b", joined[2])
        if years and role['start_date'] and role['start_date'][:4] >= years[0]:
            return "dated_company_join_and_biography_scope"
    boundary = re.search(r"prior to joining\s+(.+?)(?=[,.])", bio, re.I)
    if boundary and company_link(boundary[1], company) and found.start() < boundary.start() and company_link(bio[:found.start()], company):
        return "explicit_company_scope_before_external_history"
    for other in profile['prior_internal_roles']:
        if other is role:
            continue
        if company_link(other['position'], company) and role['start_date'] and role['start_date'] == other['start_date']:
            return "concurrent_explicit_company_office"
    raise ValueError("Prior-role employer attribution is ambiguous; 'previously served as' alone does not establish internal experience")
