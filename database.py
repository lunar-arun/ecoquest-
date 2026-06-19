from dotenv import load_dotenv
load_dotenv()

import os
import json
import uuid
import datetime
import certifi
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


class JSONCollection:
    def __init__(self, db_path, collection_name):
        self.db_path = db_path
        self.collection_name = collection_name

    def _load(self):
        if not os.path.exists(self.db_path):
            return []
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get(self.collection_name, [])
        except Exception:
            return []

    def _save(self, docs):
        data = {}
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                pass
        data[self.collection_name] = docs
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, default=self._json_serial, indent=2)

    def _json_serial(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, ObjectId):
            return str(obj)
        raise TypeError(f"Type {type(obj)} not serializable")

    def _match(self, doc, query):
        if not query:
            return True
        for k, v in query.items():
            if k == '_id':
                doc_val = str(doc.get('_id'))
                query_val = str(v)
                if doc_val != query_val:
                    return False
            elif k == '$or':
                matched_any = False
                for sub_q in v:
                    if all(doc.get(sub_k) == sub_v for sub_k, sub_v in sub_q.items()):
                        matched_any = True
                        break
                if not matched_any:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find_one(self, query=None):
        docs = self._load()
        for doc in docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    def find(self, query=None, sort=None, limit=None):
        docs = self._load()
        matched = []
        for doc in docs:
            if self._match(doc, query):
                matched.append(dict(doc))

        if sort:
            for field, order in reversed(sort):
                reverse = True if order == -1 else False
                matched.sort(key=lambda x: (x.get(field) is not None, x.get(field)), reverse=reverse)

        if limit:
            matched = matched[:limit]

        class MockCursor:
            def __init__(self, data):
                self.data = data
            def __iter__(self):
                return iter(self.data)
            def limit(self, l):
                self.data = self.data[:l]
                return self
            def sort(self, s, direction=None):
                if isinstance(s, str):
                    sort_list = [(s, direction or 1)]
                else:
                    sort_list = s
                for field, order in reversed(sort_list):
                    rev = True if order == -1 else False
                    self.data.sort(key=lambda x: (x.get(field) is not None, x.get(field)), reverse=rev)
                return self
            def __getitem__(self, index):
                return self.data[index]
            def __len__(self):
                return len(self.data)

        return MockCursor(matched)

    def insert_one(self, document):
        docs = self._load()
        doc = dict(document)
        if '_id' not in doc:
            doc['_id'] = str(uuid.uuid4())
        docs.append(doc)
        self._save(docs)

        class InsertResult:
            def __init__(self, inserted_id):
                self.inserted_id = inserted_id
        return InsertResult(doc['_id'])

    def insert_many(self, documents):
        docs = self._load()
        inserted_ids = []
        for doc in documents:
            d = dict(doc)
            if '_id' not in d:
                d['_id'] = str(uuid.uuid4())
            docs.append(d)
            inserted_ids.append(d['_id'])
        self._save(docs)

        class InsertManyResult:
            def __init__(self, ids):
                self.inserted_ids = ids
        return InsertManyResult(inserted_ids)

    def update_one(self, query, update, upsert=False):
        docs = self._load()
        found = False
        for i, doc in enumerate(docs):
            if self._match(doc, query):
                found = True
                updated_doc = self._apply_update(doc, update)
                docs[i] = updated_doc
                break
        if not found and upsert:
            new_doc = dict(query)
            new_doc = self._apply_update(new_doc, update)
            if '_id' not in new_doc:
                new_doc['_id'] = str(uuid.uuid4())
            docs.append(new_doc)
            found = True

        if found:
            self._save(docs)

        class UpdateResult:
            def __init__(self, matched_count, modified_count):
                self.matched_count = matched_count
                self.modified_count = modified_count
        return UpdateResult(1 if found else 0, 1 if found else 0)

    def update_many(self, query, update):
        docs = self._load()
        modified_count = 0
        for i, doc in enumerate(docs):
            if self._match(doc, query):
                updated_doc = self._apply_update(doc, update)
                docs[i] = updated_doc
                modified_count += 1
        if modified_count > 0:
            self._save(docs)

        class UpdateResult:
            def __init__(self, matched_count, modified_count):
                self.matched_count = matched_count
                self.modified_count = modified_count
        return UpdateResult(modified_count, modified_count)

    def _apply_update(self, doc, update):
        doc_copy = dict(doc)
        for op, val in update.items():
            if op == '$set':
                for k, v in val.items():
                    doc_copy[k] = v
            elif op == '$inc':
                for k, v in val.items():
                    doc_copy[k] = doc_copy.get(k, 0) + v
            elif op == '$push':
                for k, v in val.items():
                    if k not in doc_copy or doc_copy[k] is None:
                        doc_copy[k] = []
                    if isinstance(v, dict) and '$each' in v:
                        doc_copy[k].extend(v['$each'])
                    else:
                        doc_copy[k].append(v)
            elif op == '$pull':
                for k, v in val.items():
                    if k in doc_copy and isinstance(doc_copy[k], list):
                        doc_copy[k] = [item for item in doc_copy[k] if item != v]
        return doc_copy

    def delete_one(self, query):
        docs = self._load()
        found_idx = -1
        for i, doc in enumerate(docs):
            if self._match(doc, query):
                found_idx = i
                break
        if found_idx != -1:
            docs.pop(found_idx)
            self._save(docs)

        class DeleteResult:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count
        return DeleteResult(1 if found_idx != -1 else 0)

    def delete_many(self, query):
        docs = self._load()
        initial_len = len(docs)
        docs = [doc for doc in docs if not self._match(doc, query)]
        deleted_count = initial_len - len(docs)
        if deleted_count > 0:
            self._save(docs)

        class DeleteResult:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count
        return DeleteResult(deleted_count)

    def count_documents(self, query):
        docs = self._load()
        count = 0
        for doc in docs:
            if self._match(doc, query):
                count += 1
        return count


class JSONDatabaseWrapper:
    def __init__(self, db_path):
        self.db_path = db_path
        self._collections = {}

    def __getattr__(self, name):
        if name not in self._collections:
            self._collections[name] = JSONCollection(self.db_path, name)
        return self._collections[name]

    def __getitem__(self, name):
        return getattr(self, name)


def get_db():
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    try:
        print(f"Connecting to MongoDB at {mongo_uri}...")
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            tlsCAFile=certifi.where()
        )
        client.admin.command('ping')
        print("Successfully connected to MongoDB.")
        return client['ecoquest']
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        print(f"MongoDB connection failed: {e}")
        db_path = os.environ.get("MOCK_DB_PATH", "ecoquest_db.json")
        print(f"Falling back to local JSON database at {db_path}...")
        return JSONDatabaseWrapper(db_path)