import numpy as np

from lib.embeddings import pack_embedding, unpack_embedding, cosine_similarity, embed_text


def test_pack_unpack_round_trip():
    vec = [1.0, 2.0, 3.0, -4.5]
    blob = pack_embedding(vec)
    result = unpack_embedding(blob)
    assert np.allclose(result, vec, atol=1e-5)


def test_cosine_similarity_identical_vectors():
    a = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, a) == pytest_approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest_approx(0.0)


def test_cosine_similarity_zero_vector_no_crash():
    a = [0.0, 0.0]
    b = [1.0, 1.0]
    assert cosine_similarity(a, b) == 0.0


def test_embed_text_returns_expected_shape():
    vec = embed_text("fixing a bug in the login form")
    assert isinstance(vec, np.ndarray)
    assert vec.shape[0] == 384  # BAAI/bge-small-en-v1.5 dimensionality


def pytest_approx(value, tol=1e-4):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol
    return _Approx()
