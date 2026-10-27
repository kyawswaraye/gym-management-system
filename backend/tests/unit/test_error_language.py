from __future__ import annotations

import importlib
from pathlib import Path

from django.test import RequestFactory
from rest_framework.exceptions import ValidationError

from apps.common.api.error_catalog import translate_error_text
from apps.common.api.exceptions import AuthenticationFailed, api_exception_handler


def test_accept_language_my_translates_the_message_and_keeps_the_code() -> None:
    request = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE="my")
    body = api_exception_handler(AuthenticationFailed(), {"request": request}).data
    assert body["error"]["code"] == "AUTHENTICATION_FAILED"
    assert body["error"]["message"] == "အထောက်အထားများ မမှန်ကန်ပါ။"


def test_accept_language_mm_is_an_alias_and_english_stays_the_default() -> None:
    myanmar = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE="mm,en;q=0.8")
    english = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE="en")
    missing = RequestFactory().get("/")
    mm_body = api_exception_handler(AuthenticationFailed(), {"request": myanmar}).data
    en_body = api_exception_handler(AuthenticationFailed(), {"request": english}).data
    default_body = api_exception_handler(AuthenticationFailed(), {"request": missing}).data
    assert mm_body["error"]["message"] == "အထောက်အထားများ မမှန်ကန်ပါ။"
    assert en_body["error"]["message"] == "Authentication credentials are invalid."
    assert default_body["error"]["message"] == "Authentication credentials are invalid."
    assert en_body["error"]["code"] == "AUTHENTICATION_FAILED"


def test_field_text_is_translated_and_unknown_text_stays_english() -> None:
    request = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE="my-MM")
    error = ValidationError({"status": ["Only a draft payment can be edited."]})
    body = api_exception_handler(error, {"request": request}).data
    assert body["error"]["fields"]["status"] == ["ငွေပေးချေမှုမူကြမ်းကိုသာ ပြင်ဆင်နိုင်သည်။"]
    assert (
        translate_error_text("This sentence is not catalogued.", myanmar=True)
        == "This sentence is not catalogued."
    )


def test_interpolated_limits_keep_their_numbers() -> None:
    assert (
        translate_error_text("Enter a value from 1 to 20.", myanmar=True)
        == "1 မှ 20 အတွင်း တန်ဖိုးတစ်ခု ထည့်ပါ။"
    )
    assert (
        translate_error_text("Check-out can only be backdated 24 hours.", myanmar=True)
        == "ထွက်ခွာမှုကို နာရီ ၂၄ သာ နောက်ကြောင်းပြန် မှတ်နိုင်သည်။"
    )


def test_every_coded_error_has_myanmar_text() -> None:
    import apps
    from apps.common.api.error_catalog import myanmar_messages
    from apps.common.api.exceptions import CodedAPIException

    root = Path(next(iter(apps.__path__))).resolve()
    for path in root.rglob("*.py"):
        if "migrations" in path.parts or path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "CodedAPIException" not in source:
            continue
        module = ".".join(path.relative_to(root.parent).with_suffix("").parts)
        importlib.import_module(module)

    catalog = myanmar_messages()
    seen: set[type] = set()
    pending = list(CodedAPIException.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        detail = " ".join(str(cls.default_detail).split())
        translation = catalog.get(detail)
        assert translation, cls.__name__
        assert any("\u1000" <= char <= "\u109f" for char in translation)
    assert len(seen) > 10
