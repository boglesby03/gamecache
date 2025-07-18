from decimal import Decimal
from datetime import datetime
import copy
import html
import re


articles = ['A', 'An', 'The']

# Regular expression pattern for Latin characters including accented characters to save space
# Allow special characters - add any additional ones as they come available
latin_pattern = re.compile(r'^[a-zA-Zà-ÿÀ-ßĀ-ž0-9\:\-\%\&—–\,\'\`\"\$\(\)\.\!\/\s]+$')

PUBLIC_DOMAIN_PUBLISHER=171
class BoardGame:
    def __init__(self, game_data, collection_data, expansions=[], accessories=[]):
        self.id = game_data["id"]

        name = collection_data["name"]
        if len(name) == 0:
            name = game_data["name"]

        alt_names = self.gen_name_list(game_data, collection_data)
        self.alternate_names = list(dict.fromkeys(alt_names)) # De-dupe the list, keeping order

        title = name.split()
        if title[0] in articles:
            name = ' '.join(title[1:]) + ", " + title[0]

        self.tags = collection_data["tags"]

        # TODO put this in external datamap - tag -> label
        # if 'preordered' in self.tags:
        #     name += " [Preorder]"
        # elif 'wishlist' in self.tags:
        #     name += " [Wishlist]"
        self.wl_exp = list(filter(lambda e: 'wishlist' in e.tags, expansions))
        self.wl_acc = list(filter(lambda e: 'wishlist' in e.tags, accessories))
        self.po_exp = list(filter(lambda e: 'preordered' in e.tags, expansions))
        self.po_acc = list(filter(lambda e: 'preordered' in e.tags, accessories))
        self.expansions = list(filter(lambda e: 'own' in e.tags, expansions))
        self.accessories = list(filter(lambda e: 'own' in e.tags, accessories))

        self.name = name

        self.description = html.unescape(game_data["description"])
        self.categories = game_data["categories"]
        self.mechanics = game_data["mechanics"]
        self.contained = game_data["contained"]
        self.families = game_data["families"]
        self.artists = game_data["artists"]
        self.designers = game_data["designers"]
        self.publishers = self.publisher_filter(game_data["publishers"], collection_data)
        self.reimplements = list(filter(lambda g: g["inbound"], game_data["reimplements"]))
        self.reimplementedby = list(filter(lambda g: not g["inbound"], game_data["reimplements"]))
        self.integrates = game_data["integrates"]
        self.players = self.calc_num_players(game_data, self.expansions)
        self.weight = self.calc_weight(game_data)
        self.weightRating = float(game_data["weight"]) if game_data["weight"].strip() else -1
        self.year = game_data["year"]
        self.playing_time = self.calc_playing_time(game_data)
        self.min_age = self.calc_min_age(game_data)
        self.rank = self.calc_rank(game_data)
        self.other_ranks = self.filter_other_ranks(game_data)
        self.usersrated = self.calc_usersrated(game_data)
        self.numowned = self.calc_numowned(game_data)
        self.average = self.calc_average(game_data)
        self.rating = self.calc_rating(game_data)
        self.suggested_age = self.calc_suggested_age(game_data)
        self.numplays = collection_data["numplays"]
        self.image = collection_data["image_version"] or collection_data["image"] or game_data["image"]
        self.thumbnail = collection_data["thumbnail_version"] or collection_data["thumbnail"] or game_data["thumbnail"]
        self.tags = collection_data["tags"]
        self.comment = collection_data["comment"]
        self.wishlist_comment = collection_data["wishlist_comment"]
        self.wishlist_priority = self.calc_wishlist_priority(collection_data)
        self.promo = self.is_promo()

        self.previous_players = None
        if "players" in collection_data:
            self.previous_players = list(set(collection_data["players"]))

        self.lastplayed = None
        if "last_played" in collection_data:
            self.lastplayed = collection_data["last_played"]

        self.firstplayed = None
        if "first_played" in collection_data:
            self.firstplayed = collection_data["first_played"]

        self.lastmodified = collection_data["last_modified"] # datetime.strptime(collection_data["last_modified"], '%Y-%m-%d %H:%M:%S').timestamp()
        self.version_name = collection_data["version_name"]
        self.version_year = collection_data["custom_version_year"] or collection_data["version_year"]
        self.collection_id = collection_data["collection_id"]

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return (self.__class__ == other.__class__ and self.id == other.id)

    def publisher_filter(self, publishers, game):
        publisher_list = []
        for pub in copy.deepcopy(publishers):

            if pub["id"] == PUBLIC_DOMAIN_PUBLISHER:  # (Public Domain)
                pub["flag"] = "own"
                publisher_list.clear()
                publisher_list.append(pub)
                break
            if pub["id"] in game["publisher_ids"]:
                pub["flag"] = "own"
            if pub["id"] == game["version_publisher"]:
                pub["flag"] = "own"

            publisher_list.append(pub)

        return publisher_list

    def is_promo(self):
        cat_match = any(item["name"].split(':', 1)[0] == 'Promotional' for item in self.families)
        name_match = re.search(r'\bPromo(nal|tions?|s?)\b', self.name, re.IGNORECASE) is not None
        return cat_match or name_match

    def calc_num_players(self, game_data, expansions):
        num_players = game_data["suggested_numplayers"].copy()

        for supported_num in range(game_data["min_players"], game_data["max_players"] + 1):
            if supported_num > 0 and str(supported_num) not in [num for num, _ in num_players]:
                num_players.append((str(supported_num), "sup"))

        # Add number of players from expansions
        for expansion in expansions:
            # First add all the recommended player counts from expansions, then look for additional counts that are just supported.
            for expansion_num, support in expansion.players:
                if expansion_num not in [num for num, _ in num_players]:
                    if support != "sup":
                        num_players.append((expansion_num, "exp"))
            for expansion_num, support in expansion.players:
                if expansion_num not in [num for num, _ in num_players]:
                    if support == "sup":
                        num_players.append((expansion_num, "exp_s"))

        num_players = sorted(num_players, key=lambda x: int(x[0].replace("+", "")))

        # Remove "+ player counts if they are not the last in the list
        # Also remove player counts >=14, except for the max player count (e.g. 1-100 suggested player counts will only list 1-13,100)
        num_players[:-1] = [ player for player in num_players[:-1] if player[0][-1] != "+" and int(player[0]) < 14 ]

        return num_players

    def calc_playing_time(self, game_data):
        playing_time_mapping = {
            30: '< 30min',
            60: '30min - 1h',
            120: '1-2h',
            180: '2-3h',
            240: '3-4h',
        }
        for playing_time_max, playing_time in playing_time_mapping.items():
            if not game_data["playing_time"]:
                return 'Unknown'
            if playing_time_max > int(game_data["playing_time"]):
                return playing_time

        return '> 4h'

    def calc_wishlist_priority(self, collection_data):
        priority_mapping = {
            '': 'Own',
            '0': 'Own',
            '1': 'Must Have',
            '2': 'Love to Have',
            '3': 'Like to Have',
            '4': 'Thinking About It',
            '5': 'Don\'t Buy'
        }

        if "preordered" in collection_data["tags"]:
            return "Preorder"

        if "wishlist_priority" in collection_data:
            return priority_mapping[collection_data["wishlist_priority"]]

        return "Own"

    def calc_min_age(self, game_data):
        if "min_age" not in game_data or not game_data["min_age"]:
            return None

        min_age = int(game_data["min_age"])
        if min_age == 0:
            return None

        return min_age

    def calc_rank(self, game_data):
        if not game_data["rank"] or game_data["rank"] == "Not Ranked":
            return None

        return Decimal(game_data["rank"])

    def calc_usersrated(self, game_data):
        if not game_data["usersrated"]:
            return 0

        return Decimal(game_data["usersrated"])

    def calc_numowned(self, game_data):
        if not game_data["numowned"]:
            return 0

        return Decimal(game_data["numowned"])

    def calc_rating(self, game_data):
        if not game_data["rating"]:
            return None

        return Decimal(game_data["rating"])

    def calc_average(self, game_data):
        if not game_data["average"]:
            return None

        return Decimal(game_data["average"])

    def calc_weight(self, game_data):

        if not game_data.get("weight"):
            return None
        return Decimal(game_data["weight"])

        # weight_mapping = {
        #     -1: "Unknown",
        #     0: "Light",
        #     1: "Light",
        #     2: "Light Medium",
        #     3: "Medium",
        #     4: "Medium Heavy",
        #     5: "Heavy",
        # }

        # return weight_mapping[round(Decimal(game_data["weight"] or -1))]

    def calc_suggested_age(self, game_data):

        sum = 0
        total_votes = 0
        suggested_age = 0

        for player_age in game_data["suggested_playerages"]:
            count = player_age["numvotes"]
            sum += int(player_age["age"]) * count
            total_votes += count

        if total_votes > 0:
            suggested_age = round(sum / total_votes, 2)

        return suggested_age

    def filter_other_ranks(self, game_data):

        # Remove the BGG Rank, since it's already handled elsewhere
        other_ranks = list(filter(lambda g: g["id"] != "1" and g["value"] != "Not Ranked", game_data["other_ranks"]))

        for i, rank in enumerate(other_ranks):
            other_ranks[i]["friendlyname"] = re.sub(" Rank", "", rank["friendlyname"])

        return other_ranks

    def gen_name_list(self, game_data, collection_data):
        """rules for cleaning up linked items to remove duplicate data, such as the title being repeated on every expansion"""

        game = game_data["name"]

        game_titles = []
        game_titles.append(collection_data["name"])
        game_titles.append(game)
        game_titles.append(game.split("–")[0].strip()) # Medium Title
        game_titles.append(game.split("-")[0].strip()) # Medium Title - different dash
        game_titles.append(game.split(":")[0].strip()) # Short Title
        game_titles.append(game.split("(")[0].strip()) # No Edition

        # Carcassonne Big Box 5, Alien Frontiers Big Box, El Grande Big Box
        if any("Big Box" in title for title in game_titles):
            game_tmp = re.sub(r"\s*\(?Big Box.*", "", game, flags=re.IGNORECASE)
            game_titles.append(game_tmp)

        # TODO maybe add a rule to put title without number on the title list
        if "Air, Land, and Sea" in game_titles:
            game_titles.append("Air, Land and Sea")
        elif "Burgle Bros." in game_titles:
            game_titles.append("Burgle Bros 2")
        elif "Burgle Bros 2" in game_titles:
            game_titles.append("Burgle Bros.")
        # elif "Cartographers" in game_titles:
        #     game_titles.insert(0, "Cartographers: A Roll Player Tale")  # This needs to be first in the list
        elif "Cartographers Heroes" in game_titles:
            game_titles.append("Cartographers: A Roll Player Tale")
            game_titles.append("Cartographers")
        elif "Ca$h 'n Guns" in game_titles:
            game_titles.append("Ca$h 'n Guns (Second Edition)")
        elif "Chronicles of Crime" in game_titles:
            game_titles.insert(0, "The Millennium Series")
            game_titles.insert(0, "Chronicles of Crime: The Millennium Series")
        elif "DC Comics Deck-Building Game" in game_titles:
            game_titles.append("DC Deck-Building Game")
            game_titles.append("DC Deck Building Game")
        elif "DC Deck-Building Game" in game_titles:
            game_titles.append("DC Comics Deck-Building Game")
            game_titles.append("DC Deck Building Game")
        elif "Endeavor: Deep Sea Deluxe Edition" in game_titles:
            game_titles.append("Endeavor: Deep Sea")
        elif "Hive Pocket" in game_titles:
            game_titles.append("Hive")
        elif "Horizons of Spirit Island" in game_titles:
            game_titles.append("Spirit Island")
        elif any(title in ("King of Tokyo", "King of New York") for title in game_titles):
            game_titles.insert(0, "King of Tokyo/New York")
            game_titles.insert(0, "King of Tokyo/King of New York")
            game_titles.append("King of Tokyo")
            game_titles.append("King of New York")
        elif "Legends of Andor" in game_titles:
            game_titles.append("Die Legenden von Andor")
        elif "Massive Darkness 2" in game_titles:
            game_titles.append("Massive Darkness")
        elif "No Thanks!" in game_titles:
            game_titles.append("Schöne Sch#!?e")
        elif "Power Grid Deluxe" in game_titles:
            game_titles.append("Power Grid")
        elif "Queendomino" in game_titles:
            game_titles.append("Kingdomino")
        elif "Rivals for Catan" in game_titles:
            game_titles.append("The Rivals for Catan")
            game_titles.append("Die Fürsten von Catan")
            game_titles.append("Catan: Das Duell")
        # Collector's Edition doesn't have the full title
        elif "Robinson Crusoe" in game_titles:
            game_titles.append("Robinson Crusoe: Adventures on the Cursed Island")
            game_titles.append("Adventures on the Cursed Island")
        elif "Rolling Realms Redux" in game_titles:
            game_titles.append("Rolling Realms")
        elif "Rococo" in game_titles:
            game_titles.append("Rokoko")
        elif "Small World Underground" in game_titles:
            game_titles.append("Small World")
        elif "Survive: Escape from Atlantis" in game_titles:
            game_titles.append("Survive the Island")
        elif any(title in ("Tournament at Avalon", "Tournament at Camelot") for title in game_titles):
            game_titles.insert(0, "Tournament at Camelot/Avalon")
        # elif "Unforgiven" in game_titles:
        #     game_titles.insert(0, "Unforgiven: The Lincoln Assassination Trial")
        elif "Viticulture Essential Edition" in game_titles:
            game_titles.append("Viticulture")
        elif "Ultra Tiny Epic Galaxies" in game_titles:
            game_titles.append("Tiny Epic Galaxies")
        elif "Unmatched Adventures" in game_titles:
            game_titles.append("Unmatched")

        game_titles.extend(game_data["alternate_names"])
        #game_titles.extend([ game["name"] for game in game_data["reimplements"]])
        #game_titles.extend([ game["name"] for game in game_data["reimplementedby"]])
        #game_titles.extend([ game["name"] for game in game_data["integrates"]])

        # Scrub non-latin based titles
        tmp_titles = [ title for title in game_titles if latin_pattern.match(title)]

        return tmp_titles

    def todict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "categories": self.categories,
            "mechanics": self.mechanics,
            "players": self.players,
            "weight": self.weight,
            "playing_time": self.playing_time,
            "min_age": self.min_age,
            "rank": self.rank,
            "usersrated": self.usersrated,
            "numowned": self.numowned,
            "rating": self.rating,
            "numplays": self.numplays,
            "image": self.image,
            "thumbnail": self.thumbnail,
            "tags": self.tags,
            "previous_players": getattr(self, 'previous_players', None),
            "expansions": self.expansions,
            # Add the color field, ensuring it's handled if not present
            "color": getattr(self, 'color', None),
            "alternate_names": self.alternate_names,
            "comment": self.comment,
            "wishlist_comment": self.wishlist_comment,
            "wishlist_priority": self.wishlist_priority,
            "artists": self.artists,
            "designers": self.designers,
            "publishers": self.publishers,
            "year": self.year,
            "accessories": self.accessories,
            "families": self.families,
            "reimplements": self.reimplements,
            "reimplementedby": self.reimplementedby,
            "integrates": self.integrates,
            "wl_exp": self.wl_exp,
            "wl_acc": self.wl_acc,
            "po_exp": self.po_exp,
            "po_acc": self.po_acc,
            "contained": self.contained,
            "weightRating": self.weightRating,
            "other_ranks": self.other_ranks,
            "usersrated": self.usersrated,
            "average": self.average,
            "suggested_age": self.suggested_age,
            "last_modified": self.lastmodified,
            "version_name": self.version_name,
            "version_year": self.version_year,
            "collection_id": self.collection_id,
            "first_played": self.firstplayed,
            "last_played": self.lastplayed,
            "promo": self.promo,
        }
