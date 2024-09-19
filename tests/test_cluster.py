import uuid

from silverback.cluster.types import ClusterConfiguration


def test_hmac_signature():
    config = ClusterConfiguration()
    cluster_id = uuid.uuid4()
    owner = "0x4838B106FCe9647Bdf1E7877BF73cE8B0BAD5f97"
    product_code = config.get_product_code(owner, cluster_id)
    # NOTE: Ensure we can properly decode the encoded product code into a configuration
    assert config == ClusterConfiguration.decode(product_code[:16])
    assert config.validate_product_code(owner, product_code[16:], cluster_id)
