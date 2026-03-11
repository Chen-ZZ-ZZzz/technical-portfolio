def display_inventory(inventory):
    print("Inventory:")
    item_total = 0
    for k, v in inventory.items():
        # FILL THIS PART IN
        item_total += v
        print(str(v) + ' ' + k)
    print("\nTotal number of items: " + str(item_total))

# stuff = {'rope': 1, 'torch': 6, 'gold coin': 42, 'dagger': 1, 'arrow': 12}
# display_inventory(stuff)

def add_to_inventory(inventory, added_items):
    # Your code goes here.
    for s in added_items:
        inventory.setdefault(s, 0)
        inventory[s] += 1
    return inventory

inv = {'gold coin': 42, 'rope': 1}
dragon_loot = ['gold coin', 'dagger', 'gold coin', 'gold coin', 'ruby']
# for e in dragon_loot:
#     print(e)
inv = add_to_inventory(inv, dragon_loot)
display_inventory(inv)
