import json
import shapely.geometry as sg

def intersects(e1, e2):
    p1 = sg.Polygon(e1['positions'])
    p2 = sg.Polygon(e2['positions'])
    try:
        return p1.intersects(p2) and not p1.touches(p2)
    except:
        return False

with open('parsed_data.json') as f:
    data = json.load(f)

bg_layers = {'LightBackground', 'DarkBackground', 'IcongBackground', 'Icons', 'Legend&Logo', 'Text'}
bg_elements = [e for e in data['elements'] if e.get('layer') in bg_layers and not e.get('exhibitor_ids') and e.get('tag') == 'path' and e.get('positions')]

print(f"Total background paths: {len(bg_elements)}")

overlaps = []
for i in range(len(bg_elements)):
    for j in range(i + 1, len(bg_elements)):
        e1 = bg_elements[i]
        e2 = bg_elements[j]
        if intersects(e1, e2):
            overlaps.append((i, j, e1['guid'], e2['guid'], e1['layer'], e2['layer']))

print(f"Found {len(overlaps)} overlapping pairs.")
for i, j, g1, g2, l1, l2 in overlaps[:20]:
    print(f"Overlap: {g1} ({l1}) and {g2} ({l2}) - Doc indices: {i}, {j}")
