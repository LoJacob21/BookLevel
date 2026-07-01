from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION prevent_xptransaction_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'XPTransaction é imutável — não é permitido UPDATE em gamification_xptransaction.';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'XPTransaction é imutável — não é permitido DELETE em gamification_xptransaction.';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_xptransaction_immutable
    BEFORE UPDATE OR DELETE ON gamification_xptransaction
    FOR EACH ROW
    EXECUTE FUNCTION prevent_xptransaction_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS trg_xptransaction_immutable ON gamification_xptransaction;
DROP FUNCTION IF EXISTS prevent_xptransaction_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('gamification', '0002_alter_xptransaction_user'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
