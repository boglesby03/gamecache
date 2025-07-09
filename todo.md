
# Task List

- [X] Expansions/accessories of expansions
- [X] Still need to handle exceptions to expansions of expansions - such as calendars - again should be fixed in bgg
- [X] add publishers, designers, artists to search?
- [ ] Flag primary publisher or publisher I own - star similar to player count
- [ ] Make lists - articles, ignored expansions configurable
- [ ] Move hard-coded expansion name rules
- [X] I need a place to list expansions that have no associated games
- [X] Wishlisted & Pre-ordered games - how should I denote expansions that associate with these? different categories?
- [X] Filter by wishlist level - 1-5
- [ ] Unpublished games - better way to create entries for games that don't have a bgg entry yet
- [X] Move Accessories next to Expansions
- [X] Add expansion/accessory images on hover
- [X] Include custom notes
- [X] Handle multiple versions/copies - i.e. Similo, Get Bit Expansions
- [ ] Split family into top-level families and specifics: Theme:Art, Crowdfunding:Kickstarter, etc
- [ ] Link to boardgamearena as well?
- [X] Incorporate reimplements/reimplemented by
- [ ] Read old index to compare difference in size
- [ ] Read version name for Accessories (e.g., Rivals of Catan card holder)
- [ ] Read version name for Contains (e.g. El Grande Big Box)
- [ ] Better way to handle the Duplicates from how BGG handles Deluxe/KS versions of games
- [ ] include expansions for games that are "included"
- [X] Player ages - recommended/community
- [X] Other rankings (Family/War/etc)

# New tasks for SQLite

- [X] Fix filter sorts - shouldn't sort pre-sorted lists (number of players, playing times)
- [-] All details missing from game cards
  - [X] Hover on player count for details (recommend, specific player counts, etc)
  - [X] Hover community age
  - [X] Owned/preorders/wishlist with ranking
  - [X] Other rankings
  - [X] Search family - don't need to display
  - [ ] Display/link Artists, Designers, Publishers, Families?
- [X] Add someway to determine preorders/wishlists
- [X] Fix Wishlist rankings to sort correctly - may need to add numbers to them during indexing
- [X] Add images to rest of the fields - accessories, contains, reimplements, etc.
- [ ] Add rating (i.e. a small card) and other details to the hover image?
- [ ] Should data be split out into different tables?
- [X] Undo data saving changes - don't have to reduce comments or filter expansions
- [ ] Should I continue to split out Expansions without games into 3 entries?
- [X] Fix duplicate preorder/wishlist items
- [X] Search box needs a clear X in it
- [ ] Is there anyway to improve search - soundex, search more fields, score searches (e.g. Inis) - fd5?
- [ ] How about just updating the database rather than starting from scratch every time?

# Fields i'd like to have - but aren't available in APIs

- [ ] Version description - some games have additional details on the version details (Like Amun Re Expanded Edition)
- [ ] Short description - short blurb might be useful - those these are normally more marketing like.  Probably would use on hover of the cover art.