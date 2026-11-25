"""Site-neutral, generation-only entity grouping; not a catalog verifier."""
import unicodedata

IDENTITY_PROMPT = (
    '\nENTITY DIVERSITY IS REQUIRED: every request must refer to a DIFFERENT underlying '
    'named target, not another wording of an earlier target. Each request must explicitly '
    'name that target. Return a top-level identities array parallel to cases, one object '
    'per case: {"canonical_name":"primary item title", "aliases":["other spellings or '
    'language names of this same target"], "creator":"optional author/artist"}. For music '
    'canonical_name is the SONG TITLE, not artist plus command text; covers, remixes, live '
    'versions and translated titles of the same song belong to one group. Choose titles '
    'that are different even without artist qualifiers. Vary wording/language independently '
    'of target choice. Do not provide expected search results, URLs, playback answers or '
    'availability claims. Identity metadata is for generation duplicate checks only; do '
    'not add it to the function arguments, task template or shared contract. Do not '
    'enumerate concrete target names in that contract. Use a broad range of named targets '
    'rather than repeatedly returning the most common few. Return aliases honestly, not '
    'new invented IDs intended to evade duplicate checks.'
)


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())


def validate_identity(value):
    if not isinstance(value,dict) or set(value)-{'canonical_name','aliases','creator'}:
        raise ValueError('invalid generation identity fields')
    name=value.get('canonical_name');aliases=value.get('aliases')
    if not isinstance(name,str) or not 1<=len(name.strip())<=160 or not normalize(name):
        raise ValueError('identity needs a canonical item title')
    if not isinstance(aliases,list) or len(aliases)>8 or any(
        not isinstance(a,str) or not 1<=len(a.strip())<=160 or not normalize(a) for a in aliases):
        raise ValueError('identity aliases must be a bounded string list')
    creator=value.get('creator','')
    if not isinstance(creator,str) or len(creator)>160:
        raise ValueError('invalid identity creator')
    return {'canonical_name':name.strip(),'aliases':[a.strip() for a in aliases],'creator':creator.strip()}


def identity_keys(value):
    value=validate_identity(value)
    # Conservative: same title under another artist is still excluded.
    return {normalize(value['canonical_name']),*(normalize(a) for a in value['aliases'])}


def validate_binding(parameters, identity):
    text=normalize(' '.join(v for v in parameters.values() if isinstance(v,str)))
    if not any(key in text for key in identity_keys(identity)):
        raise ValueError('declared target title or alias is not explicit in the request')


LIMITATION = ('Groups use backend-declared canonical names/aliases plus Unicode/case/punctuation '
              'normalization. Unknown aliases, dishonest labels or unavailable/nonexistent catalog '
              'items are not ruled out; identities are not independently catalog-verified.')
