"""Unified card listing publisher — asks card type and photo folder, then routes."""
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

load_dotenv()


def main():
    print()
    print("─" * 40)
    print("  Card Publisher")
    print("─" * 40)
    print("  sports — Sports Cards")
    print("  tcg    — Pokémon / TCG")
    print()

    card_type = input("Card type [sports/tcg]: ").strip().lower()
    if card_type == "sports":
        sys.path.insert(0, str(ROOT / "listings-publisher"))
        import publish as publisher
    elif card_type == "tcg":
        sys.path.insert(0, str(ROOT / "listings-publisher"))  # for images + ebay_api
        sys.path.insert(0, str(ROOT / "pokemon-publisher"))
        import publish as publisher
    else:
        print("ERROR: enter 'sports' or 'tcg'")
        sys.exit(1)

    image_folder = input("Photo folder path (press Enter to skip if repricing only): ").strip().strip("'\"")

    publisher.main(image_folder=image_folder or None)


if __name__ == "__main__":
    main()
