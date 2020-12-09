import re

from mybgg.bgg_client import BGGClient
from mybgg.bgg_client import CacheBackendSqlite
from mybgg.models import BoardGame


class Downloader():
    def __init__(self, project_name, cache_bgg, debug=False):
        if cache_bgg:
            self.client = BGGClient(
                cache=CacheBackendSqlite(
                    path=f"{project_name}-cache.sqlite",
                    ttl=60 * 60 * 24,
                ),
                debug=debug,
            )
        else:
            self.client = BGGClient(
                debug=debug,
            )

    def collection(self, user_name, extra_params):
        collection_data = []
        plays_data = []

        if isinstance(extra_params, list):
            for params in extra_params:
                collection_data += self.client.collection(
                    user_name=user_name,
                    **params,
                )
        else:
            collection_data = self.client.collection(
                user_name=user_name,
                **extra_params,
            )

        params = {"subtype": "boardgameaccessory", "own": 1}
        accessory_data = self.client.collection(user_name=user_name, **params)
        accessory_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in accessory_data])

        plays_data = self.client.plays(
            user_name=user_name,
        )

        game_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in collection_data])
        game_id_to_tags = {game["id"]: game["tags"] for game in collection_data}
        game_id_to_image = {game["id"]: game["image_version"] or game["image"] for game in collection_data}
        game_id_to_numplays = {game["id"]: game["numplays"] for game in collection_data}

        game_id_to_players = {game["id"]: [] for game in collection_data}
        for play in plays_data:
            if play["game"]["gameid"] in game_id_to_players:
                game_id_to_players[play["game"]["gameid"]].extend(play["players"])
                game_id_to_players[play["game"]["gameid"]] = list(set(game_id_to_players[play["game"]["gameid"]]))

        games_data = list(filter(lambda x: x["type"] == "boardgame", game_list_data))
        expansions_data = list(filter(lambda x: x["type"] == "boardgameexpansion", game_list_data))

        game_id_to_accessory = {game["id"]: [] for game in games_data}
        game_id_to_expansion_accessory = {game["id"]: [] for game in expansions_data}

        game_id_to_expansion_expansions = {game["id"]: [] for game in expansions_data}
        for expansion_data in expansions_data:
            for expansion in expansion_data["expansions"]:
                if expansion["inbound"] and expansion["id"] in game_id_to_expansion_expansions:
                    game_id_to_expansion_expansions[expansion["id"]].append(expansion_data)
                if is_promo_box(expansion_data):
                    games_data.append(expansion_data)
                    game_id_to_accessory[expansion_data["id"]] = []

        for accessory_data in accessory_list_data:
            for accessory in accessory_data["accessories"]:
                if accessory["inbound"]:
                    if accessory["id"] in game_id_to_accessory:
                        game_id_to_accessory[accessory["id"]].append(accessory_data)
                    elif accessory["id"] in game_id_to_expansion_accessory:
                        game_id_to_expansion_accessory[accessory["id"]].append(accessory_data)

        game_id_to_expansion = {game["id"]: [] for game in games_data}
        for expansion_data in expansions_data:
            for expansion in expansion_data["expansions"]:
                if expansion["inbound"] and expansion["id"] in game_id_to_expansion:
                    if not is_promo_box(expansion_data):
                        game_id_to_expansion[expansion["id"]].append(expansion_data)
                        game_id_to_expansion[expansion["id"]].extend(game_id_to_expansion_expansions[expansion_data["id"]])
                        game_id_to_accessory[expansion["id"]].extend(game_id_to_expansion_accessory[expansion_data["id"]])

        games = [
            BoardGame(
                game_data,
                image=game_id_to_image[game_data["id"]],
                tags=game_id_to_tags[game_data["id"]],
                numplays=game_id_to_numplays[game_data["id"]],
                previous_players=game_id_to_players[game_data["id"]],
                expansions=set(
                    BoardGame(expansion_data)
                    for expansion_data in game_id_to_expansion[game_data["id"]]
                ),
                accessories=set(
                    BoardGame(accessory_data)
                    for accessory_data in game_id_to_accessory[game_data["id"]]
                )
            )
            for game_data in games_data
        ]

        # Cleanup the game
        for game in games:
            for exp in game.expansions:
                exp.name = remove_prefix(exp.name, game.name)
            for acc in game.accessories:
                acc.name = remove_prefix(acc.name, game.name)
            contained_list = []
            for con in game.contained:
                if con["inbound"]:
                    con["name"] = remove_prefix(con["name"], game.name)
                    contained_list.append(con)
            game.contained = contained_list

            family_list = []
            for fam in game.families:
                newFam = family_filter(fam)
                if newFam:
                    family_list.append(newFam)
            game.families = family_list

            # Ignore publishers for Public Domain games
            publisher_list = []
            for pub in game.publishers:
                if pub.id == 171:  # (Public Domain)
                    publisher_list.clear()
                    publisher_list.append(pub)
                    break
                publisher_list.append(pub)

            # Resort the list after updating the names
            game.expansions = sorted(game.expansions, key=lambda x: x.name)
            game.accessories = sorted(game.accessories, key=lambda x: x.name)
            game.contained = sorted(game.contained, key=lambda x: x["name"])
            game.families = sorted(game.families, key=lambda x: x["name"])

        return games


# May want to make other changes to the family similar to the prefix logic
def family_filter(family):
    """Filter out Admin messages"""

    group = family["name"].split(":")[0]
    if group == "Admin":
        return None

    return family

def is_promo_box(game):
    """Ignore the Deutscher Spielepreile Goodie Boxes and Brettspiel Adventskalender as expansions and treat them like base games"""

    # return game["id"] in (178656, 191779, 204573, 231506, 256951, 205611, 232298, 257590, 286086)
    # Change this to look for board game family 39378 (Box of Promos)
    return any(39378 == family["id"] for family in game["families"])


articles = ['A', 'An', 'The']
def move_article_to_end(orig):
    """Move articles to the end of the title for proper title sorting"""

    if orig == None or orig == "":
        return orig

    new_title = orig
    title = new_title.split()
    if title[0] in articles:
        new_title = ' '.join(title[1:]) + ", " + title[0]

    return new_title

def move_article_to_start(orig):
    """Move the article back to the front for string comparison"""

    if orig == None or orig == "":
        return orig

    new_title = orig
    title = orig.split(", ")
    if title[-1] in articles:
        new_title = title[-1] + " " + ", ".join(title[:-1])
    return new_title

def remove_prefix(expansion, game):
    """rules for cleaning up linked items to remove duplicate data, such as the title being repeated on every expansion"""

    game = move_article_to_start(game)
    new_exp = move_article_to_start(expansion)

    game_titles = []
    game_titles.append(game)
    game_titles.append(game.split("–")[0].strip()) # Medium Title
    game_titles.append(game.split(":")[0].strip()) # Short Title
    game_titles.append(game.split("(")[0].strip()) # No Edition

    #Carcassonne Big Box 5, Alien Frontiers Big Box, El Grande Big Box
    if any("Big Box" in title for title in game_titles):
        game_tmp = re.sub(r"\s*\(?Big Box.*", "", game, flags=re.IGNORECASE)
        game_titles.append(game_tmp)

    if "Bruge" in game_titles:
        game_titles.append("Brügge")
    elif "Chronicles of Crime" in game_titles:
        game_titles.insert(0, "The Millennium Series")
        game_titles.insert(0, "Chronicles of Crime: The Millennium Series")
    elif "Empires: Age of Discovery" in game_titles:
        game_titles.insert(0, "Glenn Drover's Empires: Age of Discovery")
    elif any(title in ("King of Tokyo", "King of New York") for title in game_titles):
        game_titles.insert(0, "King of Tokyo/New York")
        game_titles.insert(0, "King of Tokyo/King of New York")
    elif "Legends of Andor" in game_titles:
        game_titles.append("Die Legenden von Andor")
    elif "Lord of the Rings: Journeys in Middle-Earth" in game_titles:
        game_titles.append("Lord of the Rings: Journeys in Middle Earth")
    elif any(title.startswith("Neuroshima Hex") for title in game_titles):
        game_titles.append("Neuroshima Hex!")
        game_titles.append("Neuroshima Hex")
    elif "Lord of the Rings: Journeys in Middle-earth" in game_titles:
        game_titles.append("The Lord of the Rings: Journeys in Middle Earth")
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
    elif "Robinson Crusoe" in game_titles:
        # for some reason the accessories have "Adventure" instead of "Adventures"
        game_titles.insert(0, "Robinson Crusoe: Adventure on the Cursed Island")
    elif "Rococo" in game_titles:
        game_titles.append("Rokoko")
    elif "Small World Underground" in game_titles:
        game_titles.append("Small World")
    elif "Spirits of the Forest" in game_titles:
        game_titles.append("Spirit of the Forest")
    elif "Viticulture Essential Edition" in game_titles:
        game_titles.append("Viticulture")

    titles = [x.lower() for x in game_titles]

    new_exp_lower = new_exp.lower()
    for title in titles:
        if new_exp_lower.startswith(title):
            new_exp = new_exp[len(title):]
            break

    # Shorten Fan Expansions to just [Fan]
    new_exp = re.sub(r"\s*\(?Fan expans.*", " [Fan]", new_exp, flags=re.IGNORECASE)
    # Ticket to Ride Map Collection Titles are too long
    new_exp = re.sub(r"\s*Map Collection: Volume ", "Map Pack ", new_exp, flags=re.IGNORECASE)
    # Remove leading whitespace
    new_exp = re.sub(r"^\W+", "", new_exp)
    # If there is still a dash (secondary delimiter), swap it to a colon
    new_exp = re.sub(r" \– ", ": ", new_exp)
    new_exp = move_article_to_end(new_exp)

    # If we ended up removing everything - then just reset to what it started with
    if len(new_exp) == 0:
        return expansion

    return new_exp
