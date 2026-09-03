from app.services.entities import add_alias, normalize_name, resolve_or_create_entity


def test_normalize_strips_suffixes_and_case():
    assert normalize_name("NVIDIA Corporation") == "nvidia"
    assert normalize_name("Nvidia Corp.") == "nvidia"
    assert normalize_name("NVIDIA") == "nvidia"


def test_normalize_handles_accents_and_punctuation():
    assert normalize_name("Société Générale S.A.") == "societe generale"


def test_normalize_never_returns_empty():
    assert normalize_name("Inc.") == "inc."


async def test_variants_resolve_to_one_entity(session):
    a = await resolve_or_create_entity(session, "NVIDIA Corporation", "public_company", ticker="NVDA")
    b = await resolve_or_create_entity(session, "Nvidia Corp.", "public_company")
    c = await resolve_or_create_entity(session, "NVDA", "public_company")
    assert a.id == b.id == c.id


async def test_same_name_different_type_is_a_different_entity(session):
    a = await resolve_or_create_entity(session, "Apple", "public_company")
    b = await resolve_or_create_entity(session, "Apple", "product")
    assert a.id != b.id


async def test_alias_is_idempotent(session):
    entity = await resolve_or_create_entity(session, "Solid-state battery", "technology")
    first = await add_alias(session, entity, "solid state battery")
    second = await add_alias(session, entity, "Solid State Battery")
    assert first.id == second.id
