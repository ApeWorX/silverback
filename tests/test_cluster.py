from eth_utils import to_checksum_address
from hypothesis import given
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from silverback.cluster.types import ClusterConfiguration

CONFIG_SCHEMA = ClusterConfiguration.model_json_schema()


@given(  # type: ignore[call-overload]
    cluster_id=st.uuids(version=4),
    owner=st.binary(min_size=20, max_size=20).map(to_checksum_address),
    config_dict=from_schema(CONFIG_SCHEMA),
)
def test_hmac_signature(cluster_id, owner, config_dict):
    # NOTE: Ignore `version` fuzzed value
    config_dict["version"] = 1
    config = ClusterConfiguration(**config_dict)
    product_code = config.get_product_code(owner, cluster_id)
    # NOTE: There is a gap of empty bytes between 8-16
    encoded_config, sig = product_code[:8], product_code[16:]
    # NOTE: Ensure we can properly decode the encoded product code into a configuration
    assert config == ClusterConfiguration.decode(encoded_config)
    assert config.validate_product_code(owner, sig, cluster_id)
