import json
with open('parsed_data.json') as f:
    data = json.load(f)

ignored = []
bg_layers = {'LightBackground', 'DarkBackground', 'IcongBackground', 'Icons', 'Legend&Logo', 'Text', 'Booths'}
for e in data['elements']:
    if not (e.get('x') and e.get('y') and e.get('width') and e.get('height')):
        continue

    ex_ids_str = e.get('exhibitor_ids', '')
    guid = e.get('guid', '')
    special_booth = (guid == "bAWE Gaming Hub" or guid == "bAWE Gaming Stage")

    is_booth = bool(ex_ids_str or special_booth)
    is_bg = e.get('layer') in bg_layers and not ex_ids_str

    if not is_booth and not is_bg:
        ignored.append(e)

print(f"Ignored elements: {len(ignored)}")
for e in ignored:
    print(f"GUID: {e['guid']}, Layer: {e['layer']}, Fill: {e['fill']}")

# Also check for elements with same Y
y_counts = {}
for e in data['elements']:
    # This won't work because parsed_data.json doesn't have the Y assigned by build.py
    pass
