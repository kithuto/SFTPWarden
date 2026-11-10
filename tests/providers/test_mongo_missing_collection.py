import pytest

from sftpwarden.config import ProviderConfig
from sftpwarden.providers import MongoDBProvider
from sftpwarden.utils.errors import ProviderError


def test_missing_collection_is_not_an_empty_policy(install_fake_pymongo):
    databases = install_fake_pymongo()
    dsn = "mongodb://localhost/test"
    provider = MongoDBProvider(ProviderConfig(type="mongodb", dsn=dsn))
    with pytest.raises(ProviderError, match="missing"):
        provider.read()
    database = databases[(dsn, "test")]
    assert database.collections == {}
    provider.create_table()
    assert provider.read().users == []
    del database.collections[provider.config.collection]
    with pytest.raises(ProviderError, match="missing"):
        provider.read()


def test_recreated_collection_during_read_is_rejected(install_fake_pymongo, monkeypatch):
    databases = install_fake_pymongo()
    dsn = "mongodb://localhost/test"
    provider = MongoDBProvider(ProviderConfig(type="mongodb", dsn=dsn))
    provider.create_table()
    database = databases[(dsn, "test")]
    collection = database[provider.config.collection]
    original_find = collection.find

    def replaced(*args, **kwargs):
        documents = original_find(*args, **kwargs)
        del database.collections[provider.config.collection]
        database.create_collection(provider.config.collection)
        return documents

    monkeypatch.setattr(collection, "find", replaced)
    with pytest.raises(ProviderError, match="changed during"):
        provider.read()
