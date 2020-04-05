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

            # I really don't care that every publisher has published chess
            if game.id == 171:
                game.publishers = []

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

    newTitle = orig
    title = newTitle.split()
    if title[0] in articles:
        newTitle = ' '.join(title[1:]) + ", " + title[0]

    return newTitle

def move_article_to_start(orig):
    """Move the article back to the front for string comparison"""

    if orig == None or orig == "":
        return orig

    newTitle = orig
    title = orig.split(", ")
    if title[-1] in articles:
        newTitle = title[-1] + " " + ", ".join(title[:-1])
    return newTitle

def remove_prefix(expansion, game):
    """rules for cleaning up linked items to remove duplicate data, such as the title being repeated on every expansion"""

    game = move_article_to_start(game)
    newExp = move_article_to_start(expansion)

    gameMediumTitle = game.split("–")[0]
    gameShortTitle = game.split(":")[0]

    #Carcassonne Big Box 5, Alien Frontiers Big Box, El Grande Big Box
    if "Big Box" in game:
        gameMediumTitle = re.sub(r"\s*\(?Big Box.*", "", game, flags=re.IGNORECASE)
    elif game == "Bruge":
        gameMediumTitle = "Brügge"
    elif game == "Empires: Age of Discovery":
        gameMediumTitle = game
        game = "Glenn Drover's Empires: Age of Discovery"
    elif game in ("King of Tokyo", "King of New York"):
        gameShortTitle = game
        gameMediumTitle = "King of Tokyo/New York"
        game = "King of Tokyo/King of New York"
    elif game.startswith("Neuroshima Hex"):
        gameShortTitle = "Neuroshima Hex"
    elif game.startswith("No Thanks"):
        gameShortTitle = "Schöne Sch#!?e"
    elif gameShortTitle == "Power Grid Deluxe":
        gameMediumTitle = "Power Grid"
    elif gameShortTitle == "Rivals for Catan":
        newExp = remove_prefix(newExp, "The Rivals for Catan")
        newExp = remove_prefix(newExp, "Die Fürsten von Catan")
        newExp = remove_prefix(newExp, "Catan: Das Duell")
    elif gameShortTitle == "Robinson Crusoe":
        # for some reason the accessories have "Adventure" instead of "Adventures"
        gameMediumTitle = "Robinson Crusoe: Adventure on the Cursed Island"
    elif game == "Small World Underground":
        gameShortTitle = "Small World"
    elif game == "Viticulture Essential Edition":
        gameShortTitle = "Viticulture"

    game = game.lower()
    gameMediumTitle = gameMediumTitle.lower()
    gameShortTitle = gameShortTitle.lower()

    if newExp.lower().startswith(game):
        newExp = newExp[len(game):]
    elif newExp.lower().startswith(gameMediumTitle):
        newExp = newExp[len(gameMediumTitle):]
    elif newExp.lower().startswith(gameShortTitle):
        newExp = newExp[len(gameShortTitle):]

    newExp = re.sub(r"\s*\(?Fan expans.*", " [Fan]", newExp, flags=re.IGNORECASE)
    newExp = re.sub(r"\s*Map Collection: Volume ", "Map Pack ", newExp, flags=re.IGNORECASE)
    newExp = re.sub(r"^\W+", "", newExp)
    newExp = re.sub(r" \– ", ": ", newExp)
    newExp = move_article_to_end(newExp)

    if len(newExp) == 0:
        return expansion

    return newExp
