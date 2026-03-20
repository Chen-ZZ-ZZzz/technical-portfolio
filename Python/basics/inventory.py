def display_inventory(inventory: dict) -> None:
    print("Inventory:")
    for item, count in inventory.items():
        print(f'  {count} {item}')
    print(f"\nTotal number of items: {sum(inventory.values())}")


def add_to_inventory(inventory: dict, added_items: list) -> dict:
    for item in added_items:
        inventory[item] = inventory.get(item, 0) + 1
    return inventory


if __name__ == '__main__':
    inv = {'gold coin': 42, 'rope': 1}
    dragon_loot = ['gold coin', 'dagger', 'gold coin', 'gold coin', 'ruby']
    inv = add_to_inventory(inv, dragon_loot)
    display_inventory(inv)
