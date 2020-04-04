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

        game_id_to_expansion = {game["id"]: [] for game in games_data}
        for expansion_data in expansions_data:
            for expansion in expansion_data["expansions"]:
                if expansion["inbound"] and expansion["id"] in game_id_to_expansion:
                    game_id_to_expansion[expansion["id"]].append(expansion_data)


        game_id_to_accessory = {game["id"]: [] for game in games_data}
        for accessory_data in accessory_list_data:
            for accessory in accessory_data["accessory"]:
                if accessory["inbound"] and accessory["id"] in game_id_to_accessory:
                    game_id_to_accessory[accessory["id"]].append(accessory_data)

        games = [
            BoardGame(
                game_data,
                image=game_id_to_image[game_data["id"]],
                tags=game_id_to_tags[game_data["id"]],
                numplays=game_id_to_numplays[game_data["id"]],
                previous_players=game_id_to_players[game_data["id"]],
                expansions=[
                    BoardGame(expansion_data)
                    for expansion_data in game_id_to_expansion[game_data["id"]]
                ],
                accessories=[
                    BoardGame(accessory_data)
                    for accessory_data in game_id_to_accessory[game_data["id"]]
                ]
            )
            for game_data in games_data
        ]

        for game in games:
            for exp in game.expansions:
                exp.name = remove_prefix(exp.name, game.name)
            for acc in game.accessories:
                acc.name = remove_prefix(acc.name, game.name)

            # Resort the list after updating the names
            game.expansions.sort(key=lambda x: x.name)
            game.accessories.sort(key=lambda x: x.name)

        return games


articles = ['A', 'An', 'The']
def moveArticleToEnd(orig):

    newTitle = orig
    title = newTitle.split()
    if title[0] in articles:
        newTitle = ' '.join(title[1:]) + ", " + title[0]

    return newTitle

def moveArticleToStart(orig):

    newTitle = orig
    title = orig.split(", ")
    if title[-1] in articles:
        newTitle = title[-1] + " " + ", ".join(title[:-1])
    return newTitle

def remove_prefix(expansion, game):

    game = moveArticleToStart(game)
    newExp = moveArticleToStart(expansion)

    gameMediumTitle = game.split("–")[0]
    gameShortTitle = game.split(":")[0]

    if game == "Empires: Age of Discovery":
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

    newExp = re.sub(r"^\W+", "", newExp)
    newExp = re.sub(r" \– ", ": ", newExp)
    newExp = moveArticleToEnd(newExp)

    return newExp
