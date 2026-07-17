"""Offline unit coverage for the submit-boundary parameter guards.

Three server-owned invariants, provable without Mongo/slicer_cli_web:

1. *Server-owned output names* (C-01) -- ``_autofillOutputs`` ALWAYS overwrites an
   output param's ``name`` with the deterministic server-side basename and never
   honors a client-supplied one, so a crafted output name like
   ``../../../../etc/passwd`` (a worker-host path-traversal vector) cannot reach the
   CLI. Other client-supplied keys on the output value merge through; only ``name``
   is normative. The overwrite alone is not enough: the *input-derived* name
   component comes from a client-minted handle whose percent-encoded tail can
   decode to a traversal path (``%2F`` survives the handle parser's pre-decode
   slash check), so every composed component is collapsed to a separator-free
   token through ``_safeNameToken``.
2. *Declared-key-only submissions* (M-01) -- ``_rejectUndeclaredSubmitParams``
   rejects (400) any submitted key the task's CLI does not declare, while declared
   keys pass.
3. *Declared-value validation* (M-01 residual) -- ``_validateDeclaredSubmitValues``
   rejects (400, naming the parameter) a declared key whose value mismatches the
   CLI declaration: wrong scalar type, out-of-``<constraints>``-range number,
   non-member enumeration value, ill-typed vector elements, an input object
   without a ``uris`` list, or a declared OUTPUT smuggling ``uris`` (which would
   otherwise shape-match the translator's input branch and die downstream).
"""

import pytest

from girder.exceptions import RestException

from girder_volview import handles
from girder_volview.backend import slicer_spec, submit


_CLI_XML = (
    '<?xml version="1.0"?>'
    "<executable><category>Radiology</category><title>Otsu</title><parameters>"
    "<label>IO</label>"
    "<image><name>inputVolume</name><channel>input</channel></image>"
    "<double><name>threshold</name><longflag>threshold</longflag></double>"
    '<image type="label"><name>outputVolume</name><channel>output</channel></image>'
    "</parameters></executable>"
)

# runTask parses the CLI XML once and threads these structures into the submit
# helpers; the tests derive them the same way at module load.
_CLI_OUTPUTS = slicer_spec.parse_cli(_CLI_XML)["outputs"]
_CLI_DECLARED = slicer_spec.declared_params(_CLI_XML)


# ---------------------------------------------------------------------------
# C-01 — server-owned output names
# ---------------------------------------------------------------------------


def test_autofill_discards_client_output_name_traversal():
    values = submit._autofillOutputs(
        {"outputVolume": {"name": "../../../../etc/passwd"}}, _CLI_OUTPUTS, "Otsu"
    )
    name = values["outputVolume"]["name"]
    # The client-supplied traversal name is discarded; a safe server basename wins.
    assert ".." not in name
    assert "/" not in name
    assert name == "output.Otsu.outputVolume.nii.gz"


def test_autofill_overwrites_name_but_merges_other_client_keys():
    values = submit._autofillOutputs(
        {"outputVolume": {"name": "attacker-chosen", "format": "nrrd"}},
        _CLI_OUTPUTS,
        "Otsu",
    )
    out = values["outputVolume"]
    # name is server-owned and overwritten; any other key merges through.
    assert out["name"] == "output.Otsu.outputVolume.nii.gz"
    assert out["format"] == "nrrd"


def test_autofill_generates_name_for_an_unfilled_output():
    values = submit._autofillOutputs({}, _CLI_OUTPUTS, "Otsu")
    assert values["outputVolume"]["name"] == "output.Otsu.outputVolume.nii.gz"


def test_autofill_sanitizes_encoded_traversal_input_handle_name(monkeypatch):
    # C-01 round 3: the traversal can also ride in on an INPUT handle. The
    # handle grammar rejects a literal `/` in the tail but percent-DECODES it
    # afterwards, so a minted name of `safe/../../../../etc/passwd.nii.gz`
    # (encoded, one legal segment) decodes back to a path. The input-derived
    # output-name component must collapse to its basename.
    monkeypatch.setattr(handles, "getApiRoot", lambda: "api/v1")
    handle = handles.mintFileHandle("0" * 24, "safe/../../../../etc/passwd.nii.gz")
    values = submit._autofillOutputs(
        {"inputVolume": {"uris": [handle]}, "outputVolume": {}}, _CLI_OUTPUTS, "Otsu"
    )
    name = values["outputVolume"]["name"]
    assert "/" not in name
    assert ".." not in name
    assert name == "passwd.Otsu.outputVolume.nii.gz"


def test_safe_name_token_collapses_every_hostile_shape():
    # Path components (either separator) collapse to the last segment; a
    # component that is nothing but dots/spaces/separators yields the fallback.
    assert submit._safeNameToken("safe/../../etc/passwd", "x") == "passwd"
    assert submit._safeNameToken("..\\..\\windows", "x") == "windows"
    assert submit._safeNameToken(" .name. ", "x") == "name"
    assert submit._safeNameToken("plain", "x") == "plain"
    assert submit._safeNameToken("foo/..", "x") == "x"
    assert submit._safeNameToken("../", "x") == "x"
    assert submit._safeNameToken("", "x") == "x"
    assert submit._safeNameToken(None, "x") == "x"


def test_candidate_output_name_sanitizes_all_components():
    name = submit._candidateOutputName(
        "safe/../../etc/passwd", "../cli", "sub/param", ".nii.gz"
    )
    assert name == "passwd.cli.param.nii.gz"


# ---------------------------------------------------------------------------
# M-01 — reject undeclared submission keys
# ---------------------------------------------------------------------------


def test_declared_param_names_reads_inputs_outputs_and_scalars():
    assert set(slicer_spec.declared_params(_CLI_XML)) == {
        "inputVolume",
        "threshold",
        "outputVolume",
    }


def test_declared_param_names_are_independent_of_label_sections():
    # A CLI whose params are NOT grouped under a <label> still declares them:
    # the accepted-key set must not depend on UI sectioning (the label-grouped
    # spec walk drops such params, but they are real submission keys).
    xml_no_label = (
        '<?xml version="1.0"?>'
        "<executable><category>Radiology</category><title>Median</title><parameters>"
        "<image><name>inputVolume</name><channel>input</channel></image>"
        "</parameters></executable>"
    )
    assert set(slicer_spec.declared_params(xml_no_label)) == {"inputVolume"}


def test_reject_undeclared_param_raises_400_listing_offenders():
    with pytest.raises(RestException) as exc:
        submit._rejectUndeclaredSubmitParams(
            {"inputVolume": {"uris": ["x"]}, "bogus": 1, "also_bad": 2}, _CLI_DECLARED
        )
    assert exc.value.code == 400
    # The offending names are listed (sorted) so the client can see what to fix.
    assert "bogus" in str(exc.value)
    assert "also_bad" in str(exc.value)


def test_reject_undeclared_param_passes_declared_keys():
    # Declared input/scalar/output keys all pass the guard.
    submit._rejectUndeclaredSubmitParams(
        {
            "inputVolume": {"uris": ["x"]},
            "threshold": 5,
            "outputVolume": {"name": "y"},
        },
        _CLI_DECLARED,
    )


def test_reject_undeclared_param_tolerates_empty_and_none():
    submit._rejectUndeclaredSubmitParams({}, _CLI_DECLARED)
    submit._rejectUndeclaredSubmitParams(None, _CLI_DECLARED)


# ---------------------------------------------------------------------------
# M-01 residual — validate declared values against the CLI declaration
# ---------------------------------------------------------------------------

# One param of every validated shape: typed scalars (with <constraints>), both
# enumeration flavors, both vector flavors, a client-minted input, and a
# server-composed output.
_VALUES_CLI_XML = (
    '<?xml version="1.0"?>'
    "<executable><category>Radiology</category><title>Otsu</title><parameters>"
    "<label>IO</label>"
    "<image><name>inputVolume</name><channel>input</channel></image>"
    "<integer><name>iterations</name>"
    "<constraints><minimum>1</minimum><maximum>10</maximum></constraints>"
    "</integer>"
    "<double><name>threshold</name>"
    "<constraints><minimum>0</minimum><maximum>100</maximum></constraints>"
    "</double>"
    "<boolean><name>invert</name></boolean>"
    "<string><name>suffix</name></string>"
    "<string-enumeration><name>mode</name>"
    "<element>fast</element><element>accurate</element>"
    "</string-enumeration>"
    "<integer-enumeration><name>levels</name>"
    "<element>2</element><element>4</element>"
    "</integer-enumeration>"
    "<integer-vector><name>kernel</name></integer-vector>"
    "<string-vector><name>tags</name></string-vector>"
    '<image type="label"><name>outputVolume</name><channel>output</channel></image>'
    "</parameters></executable>"
)

_VALUES_DECLARED = slicer_spec.declared_params(_VALUES_CLI_XML)


def _assert_value_rejected(values, *expected_fragments):
    with pytest.raises(RestException) as exc:
        submit._validateDeclaredSubmitValues(values, _VALUES_DECLARED)
    assert exc.value.code == 400
    for fragment in expected_fragments:
        assert fragment in str(exc.value)


def test_declared_params_carries_type_constraints_and_options():
    declared = slicer_spec.declared_params(_VALUES_CLI_XML)
    assert declared["iterations"]["tag"] == "integer"
    assert declared["iterations"]["constraints"] == {"min": 1, "max": 10}
    assert declared["mode"]["options"] == ["fast", "accurate"]
    assert declared["levels"]["options"] == [2, 4]
    assert declared["outputVolume"]["channel"] == "output"
    # The name set is exactly the declared_params key set (one walk, one truth).
    assert set(slicer_spec.declared_params(_VALUES_CLI_XML)) == set(declared)


def test_validate_values_happy_path_passes():
    submit._validateDeclaredSubmitValues(
        {
            "inputVolume": {"type": "image", "uris": ["girder://x"]},
            "iterations": 5,
            "threshold": 12.5,
            "invert": True,
            "suffix": "seg",
            "mode": "fast",
            "levels": 4,
            "kernel": [3, 3, 3],
            "tags": ["a", "b"],
            "outputVolume": {"format": "nrrd"},
        },
        _VALUES_DECLARED,
    )


def test_validate_values_skips_none_and_undeclared_keys():
    # None is "unset" (the translator skips it); undeclared keys are the key
    # guard's 400, not this one's.
    submit._validateDeclaredSubmitValues(
        {"threshold": None, "bogus": object()}, _VALUES_DECLARED
    )


def test_validate_rejects_wrong_scalar_types():
    _assert_value_rejected({"threshold": "abc"}, "threshold", "expected a number")
    _assert_value_rejected({"iterations": 2.5}, "iterations", "expected an integer")
    _assert_value_rejected({"invert": "true"}, "invert", "expected a boolean")
    _assert_value_rejected({"suffix": 7}, "suffix", "expected a string")
    # bool is an int subclass in Python; it must not pass as a number.
    _assert_value_rejected({"iterations": True}, "iterations", "expected a number")


def test_validate_accepts_integral_float_for_integer():
    # JSON carries no int/float distinction; 5.0 is a valid integer value.
    submit._validateDeclaredSubmitValues({"iterations": 5.0}, _VALUES_DECLARED)


def test_validate_rejects_out_of_range_numbers():
    _assert_value_rejected({"iterations": 0}, "iterations", "minimum 1")
    _assert_value_rejected({"iterations": 11}, "iterations", "maximum 10")
    _assert_value_rejected({"threshold": 1000}, "threshold", "maximum 100")


def test_validate_rejects_non_member_enum_values():
    _assert_value_rejected({"mode": "turbo"}, "mode", "fast, accurate")
    _assert_value_rejected({"mode": 3}, "mode", "expected one of")
    _assert_value_rejected({"levels": 3}, "levels", "2, 4")


def test_validate_vector_elements():
    _assert_value_rejected({"kernel": [1, 2, "x"]}, "kernel", "an integer")
    _assert_value_rejected({"kernel": [1.5]}, "kernel", "an integer")
    _assert_value_rejected({"kernel": 7}, "kernel", "expected a list")
    _assert_value_rejected({"tags": ["a", 3]}, "tags", "a string")
    # Both wire forms the translator forwards pass: a list, or a pre-joined
    # comma-separated string.
    submit._validateDeclaredSubmitValues({"kernel": [3, 5, 7]}, _VALUES_DECLARED)
    submit._validateDeclaredSubmitValues({"kernel": "3,5,7"}, _VALUES_DECLARED)
    submit._validateDeclaredSubmitValues({"tags": "a,b"}, _VALUES_DECLARED)


def test_validate_rejects_input_without_uris_list():
    _assert_value_rejected({"inputVolume": "not-an-object"}, "inputVolume", "uris")
    _assert_value_rejected({"inputVolume": {"uris": "girder://x"}}, "inputVolume")
    _assert_value_rejected({"inputVolume": {"uris": [1, 2]}}, "inputVolume")


def test_validate_rejects_output_smuggling_uris():
    # The review's confirmed M-01 repro: an output object carrying ``uris``
    # merged through autofill and shape-matched the translator's INPUT branch,
    # losing its output-folder param and dying as an internal error. Now a
    # boundary 400.
    _assert_value_rejected(
        {"outputVolume": {"uris": ["girder://x"]}}, "outputVolume", "uris"
    )


def test_validate_values_covers_params_outside_label_sections():
    # The value walk is label-independent, like the key walk: a param declared
    # outside any <label> section is still type-checked (the label-grouped spec
    # walk drops it, but it is a real submission key).
    xml_no_label = (
        '<?xml version="1.0"?>'
        "<executable><category>Radiology</category><title>Median</title><parameters>"
        "<integer><name>radius</name>"
        "<constraints><minimum>1</minimum><maximum>5</maximum></constraints>"
        "</integer>"
        "</parameters></executable>"
    )
    declared = slicer_spec.declared_params(xml_no_label)
    with pytest.raises(RestException) as exc:
        submit._validateDeclaredSubmitValues({"radius": 9}, declared)
    assert exc.value.code == 400
    assert "radius" in str(exc.value)
    submit._validateDeclaredSubmitValues({"radius": 3}, declared)


# ---------------------------------------------------------------------------
# C-1 — a <region> param's client bounds box is inverted to Slicer's RAS
# center+radius grammar at submit. The client mints the crop box as an LPS
# min/max box; the generic list branch would comma-join it verbatim, feeding the
# CLI min/max where it reads center/radius (wrong spatial region, silently). The
# region branch is driven by the DECLARED tag, and a malformed box fails closed.
# ---------------------------------------------------------------------------

_REGION_CLI_XML = (
    '<?xml version="1.0"?>'
    "<executable><category>Radiology</category><title>Crop</title><parameters>"
    "<label>IO</label>"
    "<region><name>roi</name><channel>input</channel></region>"
    "</parameters></executable>"
)
_REGION_DECLARED = slicer_spec.declared_params(_REGION_CLI_XML)


def test_region_bounds_translate_to_ras_center_radius_wire_value():
    # LPS box [-11,-9,-22,-18,27,33] is the display-mapped form of RAS center
    # (10,20,30) + radius (1,2,3); the wire value handed to slicer must be that
    # center+radius string, never the raw min/max box.
    params, _ = submit._translateValuesToSlicerParams(
        {"roi": [-11, -9, -22, -18, 27, 33]},
        user=None,
        outputFolder=None,
        declared=_REGION_DECLARED,
    )
    assert params["roi"] == "10,20,30,1,2,3"


def test_region_bounds_fail_closed_on_malformed_box():
    for bad in ([1, 2, 3], [1, 2, 3, 4, 5, "x"], "not-a-list"):
        with pytest.raises(RestException) as exc:
            submit._translateValuesToSlicerParams(
                {"roi": bad},
                user=None,
                outputFolder=None,
                declared=_REGION_DECLARED,
            )
        assert exc.value.code == 400
        assert "roi" in str(exc.value)
