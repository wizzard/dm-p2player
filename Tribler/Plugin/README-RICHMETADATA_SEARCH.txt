--------------------------------------------------------------------------------
Rich Metadata Search README
Author: Christian Raffelsberger (UNIKLU)
Date: 2011-16-02
--------------------------------------------------------------------------------

This document briefly descibes the Rich Metadata search prototype. The prototype 
allows a user to search torrents based on different metadata tags such as title, 
genre, age rating (for a complete list of the supported tags see Section 1.1.
Please note, that the Rich Metadata API is also required:
https://ttuki.vtt.fi/svn/p2p-next/JSI/RichMetadata
(see included README for setup instructions)

Document Version history:
0.11) 
* updated search tag description to reflect changes in metadata API v0.91:
  - added 'duration' tag
  - added 'releasedate' tag
* updated paths to reflect M36 structure

0.1) initial version
	  
--------------------------------------------------------------------------------
1) Search Grammar
--------------------------------------------------------------------------------
The Rich Metadata search is based on a simple query grammar. The grammar is
parsed at the receiver and converted to a SQL query that fetches the search
results.

Expression := '(' Expression ')'
Expression := '!' Expression
Expression := Expression '&&' Expression
              Expression '||' Expression

Expression := tag '=' query string    # equals
              tag '~' query string    # like, "fuzzy equals"
              tag '!=' query string   # not equals
              tag '<' query string 
              tag '>' query string
              tag '<=' query string
              tag '>=' query string


--------------------------------------------------------------------------------
1.1) Supported Search Tags
--------------------------------------------------------------------------------
"aspectratio": the aspect ratio of the video, e.g. 16:9
"age": the minimum age that is required (integer)   
"audiocoding:": a string describing the audio codec
"bitrate:": the bitrate of the file (integer)
"captionlang": the caption language code,e.g., en for English
"channels": the number of audio channels (integer)
"duration": the duration in seconds (integer)
	(Note: within the torrent the duration is stored in ISO8601 format, however 
	seconds are easier to deal with from a user point of view) 
"fileformat": the file format, such as MP4 for an MPEG-4 container
"filesize": the size in bytes of the file (integer)
"framerate": number of video frames per second, may be a floating point number
"genre": the main genre of the content      
"height": the number of horizontal pixels (integer)
"language": language code
"originator": the name of the producer of the content
"productiondate": the production date in YYYY-MM-DD format
"producationlocation": the name of the place where the content was produced
"publisher": the name of the publisher (e.g., the creator of the torrent)
"releasedate": the release date (YYYY-MM-DD)
"releaseinfo": some information that describes this release
"signlang": language code for the sign language
"synopsis": a string containing a short description of the content
"episodetitle": the name of the episode, if appropriate for the content
"title": the main name of the content
"seriestitle": the name of the series, if appropriate for the content
"width": the number of vertical pixels (integer)
"videocoding": a string describing the video codec

These tags are all defined in the Rich Metadata specification. However, some
tags that are described there are not available in queries. These are:
content, copyrightNotice, howRelated, mediaLocator and
relatedMaterial

Those tags are not suitable for searching but may be included in the results
(e.g., a URL to a cover image).


--------------------------------------------------------------------------------
2) Examples
--------------------------------------------------------------------------------
This section shows some simple examples of search queries:

Return content with the given name that provides at least 720p resolution:
title=My Favourite Series && width>=720 && aspectratio=16:9

Internally, those query will be translated into an SQL query:
SELECT x from Richmetadata WHERE title_main="My Favourite Series" AND horizontal_size>=720 AND aspect_ratio="16:9"
(To be precise, query substitution is used to prevent SQL insertion attacks)

Return content within the Action or Science fiction genre that is appropriate
for people younger than 17:
(genre=Action || genre=Science Fiction) && age<=16


--------------------------------------------------------------------------------
3) Additional Remarks
--------------------------------------------------------------------------------

--------------------------------------------------------------------------------
3.1) Adding a New Search Tag
--------------------------------------------------------------------------------
To add a new search tag to the grammar, add it to the "fieldToColumnDict" in the
"Tribler/Core/CacheDB/RichMetadataSearchGrammar" module. This dictionary maps 
search tags to the names of the according columns (this is done to decouple easy
to use search tags from possibly complex column names).

Additionally, add a mapping to the dictionary that can be found in the 
_get_richmeta_db_dict() method within the module 
"Core/CacheDB/SqliteCacheDBHandler". This dictionary contains mappings from
the tag names of the Rich Metadata API to the database columns.
The field names are derived from the API methods. Example: if the API provides a 
method "getTagName" the "get" prefix is striped and the string lowered. 
So a possible mapping could look like "tagname":"column_to_store_tag".

If the new tag should also be returned in the results, add it within in the
search() method of the RichMetadataDBHandler (part of SqliteCacheDBHandler).
Also update the Plugin/Search/hit2nsmetahit() and 
Plugin/Search/nsmetahit2nsmetarepr() methods with the new tag.

Update the database to include the new tag (Tribler/Core/CacheDB/sqlitecachdedb)


--------------------------------------------------------------------------------
3.2) Rich Metadata Search Demonstration
--------------------------------------------------------------------------------
The query processing takes place in the "Plugin/Search" module. A simple demo
page can be found in the same directory (searchpage_new.html). To try out the 
demo, the "Swarmengine" has to be started first (the overlay must be enabled).

The search page includes an OpenSearch description. If you use a browser that
supports OpenSearch, you should be able to add the NSSA search engine to your 
browser's search engines. Afterwards, you can search for torrents without
visiting a designated search page but using the text field (input the search
grammar according to Sections 1 and 2).