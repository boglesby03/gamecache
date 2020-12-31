from decimal import Decimal
import html
import re


articles = ['A', 'An', 'The']

class BoardGame:
    def __init__(self, game_data, collection_data, expansions=[], accessories=[]):
        self.id = game_data["id"]

        name = collection_data["name"]
        if len(name) == 0:
            name = game_data["name"]

        title = name.split()
        if title[0] in articles:
            name = ' '.join(title[1:]) + ", " + title[0]

        self.name = name
        
        self.alternate_names = self.gen_name_list(game_data)
        self.alternate_names.append(name)

        self.description = html.unescape(game_data["description"])
        self.categories = game_data["categories"]
        self.mechanics = game_data["mechanics"]
        self.contained = game_data["contained"]
        self.families = game_data["families"]
        self.artists = game_data["artists"]
        self.designers = game_data["designers"]
        self.publishers = game_data["publishers"]
        self.reimplements = game_data["reimplements"]
        self.integrates = game_data["integrates"]
        self.players = self.calc_num_players(game_data, expansions)
        self.weight = self.calc_weight(game_data)
        self.playing_time = self.calc_playing_time(game_data)
        self.rank = self.calc_rank(game_data)
        self.usersrated = self.calc_usersrated(game_data)
        self.numowned = self.calc_numowned(game_data)
        self.average = self.calc_average(game_data)
        self.rating = self.calc_rating(game_data)
        self.numplays = collection_data["numplays"]
        self.image = collection_data["image_version"] or collection_data["image"]
        self.tags = collection_data["tags"]
        if "players" in collection_data:
            self.previous_players = list(set(collection_data["players"]))
        self.expansions = expansions
        self.accessories = accessories

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return (self.__class__ == other.__class__ and self.id == other.id)

    def calc_num_players(self, game_data, expansions):
        num_players = game_data["suggested_numplayers"].copy()

        # Add number of players from expansions
        for expansion in expansions:
            for expansion_num, _ in expansion.players:
                if expansion_num not in [num for num, _ in num_players]:
                    num_players.append((expansion_num, "expansion"))

        num_players = sorted(num_players, key=lambda x: int(x[0].replace("+", "")))
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
        weight_mapping = {
            -1: "Unknown",
            0: "Light",
            1: "Light",
            2: "Light Medium",
            3: "Medium",
            4: "Medium Heavy",
            5: "Heavy",
        }

        return weight_mapping[round(Decimal(game_data["weight"] or -1))]

    def gen_name_list(self, game_data):
        """rules for cleaning up linked items to remove duplicate data, such as the title being repeated on every expansion"""

        game = game_data["name"]

        game_titles = []
        game_titles.append(game)
        game_titles.append(game.split("–")[0].strip()) # Medium Title
        game_titles.append(game.split(":")[0].strip()) # Short Title
        game_titles.append(game.split("(")[0].strip()) # No Edition

        #Carcassonne Big Box 5, Alien Frontiers Big Box, El Grande Big Box
        if any("Big Box" in title for title in game_titles):
            game_tmp = re.sub(r"\s*\(?Big Box.*", "", game, flags=re.IGNORECASE)
            game_titles.append(game_tmp)

        if "Chronicles of Crime" in game_titles:
            game_titles.insert(0, "The Millennium Series")
            game_titles.insert(0, "Chronicles of Crime: The Millennium Series")
        elif any(title in ("King of Tokyo", "King of New York") for title in game_titles):
            game_titles.insert(0, "King of Tokyo/New York")
            game_titles.insert(0, "King of Tokyo/King of New York")
        elif "Legends of Andor" in game_titles:
            game_titles.append("Die Legenden von Andor")
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
        elif "Rococo" in game_titles:
            game_titles.append("Rokoko")
        elif "Small World Underground" in game_titles:
            game_titles.append("Small World")
        elif "Viticulture Essential Edition" in game_titles:
            game_titles.append("Viticulture")

        game_titles.extend(game_data["alternate_names"])
        game_titles.extend([ game["name"] for game in game_data["reimplements"]])
        game_titles.extend([ game["name"] for game in game_data["integrates"]])

        # titles = [x.lower() for x in game_titles]

        return game_titles
