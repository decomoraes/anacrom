---
name: uo-commerce
description: Money on the Ultima Online shard - buying from and selling to NPC vendors, and using a bank box to store gold and goods. Use when shopping, restocking, offloading loot, or banking.
---

# Vendors and banking

## Vendors

Double click the vendor to open their list, then read it:

```
uo use 0x...        # the vendor
uo vendor 0x...     # their stock, with prices
uo buy 0x... <item_serial> <amount>
```

Selling is the same shape: `uo sell 0x... <item_serial> <amount>`. A vendor
only buys what they deal in, so a failed sale usually means wrong vendor, not
wrong command - `uo journal` says which.

Prices are per unit and your gold is in `uo status`. Check weight before buying
in bulk; an overloaded character cannot move.

## Banking

Say `bank` out loud within range of a banker and your bank box opens:

```
uo say bank
uo contents <bank_serial>
```

The bank box is a container like any other, so `uo grab` and `uo drop --into`
move things in and out. Gold in the bank is safe from death; gold on you is not.

Record the bank the first time you find one - you will come back constantly:

```
uo place here "Britain bank" --kind bank
uo place near --kind bank
```
