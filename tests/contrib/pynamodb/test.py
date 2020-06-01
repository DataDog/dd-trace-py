from pynamodb.models import Model
from pynamodb.attributes import UnicodeAttribute

class Test(Model):
  class Meta:
    table_name='Test'
    region = 'us-west-1'
    write_capacity_units = 1
    read_capacity_units = 1

  title = UnicodeAttribute(hash_key=True)