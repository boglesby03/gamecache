import itertools
import re

from mybgg.bgg_client import BGGClient
from mybgg.bgg_client import CacheBackendSqlite
from mybgg.models import BoardGame

from multidict import MultiDict

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
        accessory_collection = self.client.collection(user_name=user_name, **params)
        accessory_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in accessory_collection])
        accessory_collection_by_id = MultiDict()
        for acc in accessory_collection:
            accessory_collection_by_id.add(str(acc["id"]), acc)

        plays_data = self.client.plays(
            user_name=user_name,
        )

        game_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in collection_data])

        collection_by_id = MultiDict();
        for item in collection_data:
            item["players"] = []
            collection_by_id.add(str(item["id"]), item)

        for play in plays_data:
            play_id = str(play["game"]["gameid"])
            if play_id in collection_by_id:
                collection_by_id[play_id]["players"].extend(play["players"])

        games_data = list(filter(lambda x: x["type"] == "boardgame", game_list_data))
        expansions_data = list(filter(lambda x: x["type"] == "boardgameexpansion", game_list_data))

        game_data_by_id = {}
        expansion_data_by_id = {}

        for game in games_data:
            game["accessories_collection"] = []
            game["expansions_collection"] = []
            game_data_by_id[game["id"]] = game

        for expansion in expansions_data:
            expansion["accessories_collection"] = []
            expansion["expansions_collection"]  = []
            expansion_data_by_id[expansion["id"]] = expansion

        for expansion_data in expansion_data_by_id.values():
           # expansion_data = expansion_data_by_id[exp_id]
            if is_promo_box(expansion_data):
                game_data_by_id[expansion_data["id"]] = expansion_data
            for expansion in expansion_data["expansions"]:
                id = expansion["id"]
                if expansion["inbound"] and id in expansion_data_by_id:
                    expansion_data_by_id[id]["expansions_collection"].append(expansion_data)                                     

        for accessory_data in accessory_list_data:
            for accessory in accessory_data["accessories"]:
                id = accessory["id"]
                if accessory["inbound"]:
                    if id in game_data_by_id:
                        game_data_by_id[id]["accessories_collection"].append(accessory_data)
                    elif id in expansion_data_by_id:
                        expansion_data_by_id[id]["accessories_collection"].append(accessory_data)

        #game_id_to_expansion = {game["id"]: [] for game in games_data}
        for expansion_data in expansion_data_by_id.values():
            for expansion in expansion_data["expansions"]:
                id = expansion["id"]
                if expansion["inbound"] and id in game_data_by_id:
                    if not is_promo_box(expansion_data):
                        game_data_by_id[id]["expansions_collection"].append(expansion_data)
                        game_data_by_id[id]["expansions_collection"].extend(expansion_data_by_id[expansion_data["id"]]["expansions_collection"])
                        game_data_by_id[id]["accessories_collection"].extend(expansion_data_by_id[expansion_data["id"]]["accessories_collection"])


        games_collection = list(filter(lambda x: x["id"] in game_data_by_id, collection_by_id.values()))

        games = [
            BoardGame(
                game_data_by_id[collection["id"]],
                collection,
                expansions=[
                    BoardGame(expansion_data, collection)
                    for expansion_data in _uniq(game_data_by_id[collection["id"]]["expansions_collection"])
                    for collection in collection_by_id.getall(str(expansion_data["id"]))
                ],
                accessories=[
                    BoardGame(accessory_data, collection)
                    for accessory_data in _uniq(game_data_by_id[collection["id"]]["accessories_collection"])
                    for collection in accessory_collection_by_id.getall(str(accessory_data["id"]))
                ]
            )
            for collection in games_collection
        ]

        # Cleanup the game
        for game in games:
            for exp in game.expansions:
                exp.name = remove_prefix(exp.name, game)
            for acc in game.accessories:
                acc.name = remove_prefix(acc.name, game)
            contained_list = []
            for con in game.contained:
                if con["inbound"]:
                    con["name"] = remove_prefix(con["name"], game)
                    contained_list.append(con)
            game.contained = sorted(contained_list, key=lambda x: x["name"])

            integrates_list = []
            for integrate in game.integrates:
                # Filter integrates to owned games
                if str(integrate["id"]) in collection_by_id:
                    integrate["name"] = move_article_to_end(integrate["name"])
                    integrates_list.append(integrate)
            game.integrates = sorted(integrates_list, key=lambda x: x["name"])

            for reimps in game.reimplements:
                reimps["name"] =  move_article_to_end(reimps["name"])

            family_list = []
            for fam in game.families:
                newFam = family_filter(fam)
                if newFam:
                    family_list.append(newFam)
            game.families = family_list

            game.publishers = publisher_filter(game.publishers, collection_by_id[str(game.id)])

            # Resort the list after updating the names
            game.expansions = sorted(game.expansions, key=lambda x: x.name)
            game.accessories = sorted(game.accessories, key=lambda x: x.name)
            game.contained = sorted(game.contained, key=lambda x: x["name"])
            game.families = sorted(game.families, key=lambda x: x["name"])

        return games

def _uniq(lst):
    lst = sorted(lst, key=lambda x: x['id'])
    for _, grp in itertools.groupby(lst, lambda d: (d['id'])):
        yield list(grp)[0]

# Ignore publishers for Public Domain games
def publisher_filter(publishers, publisher_version):
    publisher_list = []
    for pub in publishers:
        if pub["id"] == 171:  # (Public Domain)
            publisher_list.clear()
            publisher_list.append(pub)
            break
        if pub["id"] == publisher_version["publisher_id"]:
            pub["flag"] = "own"
        publisher_list.append(pub)

    return publisher_list


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

def remove_prefix(expansion, game_details):
    """rules for cleaning up linked items to remove duplicate data, such as the title being repeated on every expansion"""

    new_exp = move_article_to_start(expansion)

    game_titles = game_details.alternate_names
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
