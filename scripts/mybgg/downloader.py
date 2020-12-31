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
        accessory_collection = self.client.collection(user_name=user_name, **params)
        accessory_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in accessory_collection])
        accessory_collection_by_id = {acc["id"]: acc for acc in accessory_collection }

        plays_data = self.client.plays(
            user_name=user_name,
        )

        game_list_data = self.client.game_list([game_in_collection["id"] for game_in_collection in collection_data])

        collection_by_id = {game["id"]: game for game in collection_data}
        for id in collection_by_id:
            collection_by_id[id]["players"] = []

        for play in plays_data:
            play_id = play["game"]["gameid"]
            if play_id in collection_by_id:
                collection_by_id[play_id]["players"].extend(play["players"])

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
                collection_by_id[game_data["id"]],
                expansions=set(
                    BoardGame(expansion_data, collection_by_id[expansion_data["id"]])
                    for expansion_data in game_id_to_expansion[game_data["id"]]
                ),
                accessories=set(
                    BoardGame(accessory_data, accessory_collection_by_id[accessory_data["id"]])
                    for accessory_data in game_id_to_accessory[game_data["id"]]
                )
            )
            for game_data in games_data
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
            game.contained = contained_list

            family_list = []
            for fam in game.families:
                newFam = family_filter(fam)
                if newFam:
                    family_list.append(newFam)
            game.families = family_list

            game.publishers = publisher_filter(game.publishers)

            # Resort the list after updating the names
            game.expansions = sorted(game.expansions, key=lambda x: x.name)
            game.accessories = sorted(game.accessories, key=lambda x: x.name)
            game.contained = sorted(game.contained, key=lambda x: x["name"])
            game.families = sorted(game.families, key=lambda x: x["name"])

        return games

# Ignore publishers for Public Domain games
def publisher_filter(publishers):
    publisher_list = []
    for pub in publishers:
        if pub["id"] == 171:  # (Public Domain)
            publisher_list.clear()
            publisher_list.append(pub)
            break
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
