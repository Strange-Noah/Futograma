import unittest,sqlite3
from squad_pipeline.source_player_registration import register
class NewSourceDuplicateTests(unittest.TestCase):
 def test_changed_provider_url_cannot_create_duplicate_of_possible_existing_person(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript("CREATE TABLE players(player_id INTEGER,display_name TEXT);CREATE TABLE validation_player_aliases(player_id INTEGER,identity_json TEXT);INSERT INTO players VALUES(1,'Example Player');")
  with self.assertRaisesRegex(ValueError,'POSSIBLE_EXISTING_CANONICAL'):
   register(db,1,dict(name='Example Middle Player',provider_key='https://torlytics.de/spieler/example-middle-player'),dict(url='https://torlytics.de/spiel/example',sha256='a'*64))
  self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],1);db.close()
