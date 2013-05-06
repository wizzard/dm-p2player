# Written by Christian Raffelsberger
# see LICENSE.txt for license information

import sys
import yacc
import lex

# Code for parsing a RichMeta query and producing valid SQL
# maps the search terms to the actual columns in the database

DEBUG = False

# stores all encountered fields within a query.
fields = list()

# maps the query terms to the database columns
fieldToColumnDict = {
            "aspectratio":"aspect_ratio", 
            "audiocoding:":"audio_coding", 
            "bitrate:":"bit_rate",
            "captionlang":"caption_language",
            "duration":"duration",     
            "fileformat":"file_format",
            "filesize":"file_size",        
            "framerate":"frame_rate",         
            "genre":"genre",             
            "width":"horizontal_size",   
            "language":"language",          
            "age":"minimum_age",        
            "channels":"num_of_channels",    
            "originator":"originator",        
            "productiondate":"production_date",    
            "producationlocation":"production_location",     
            "publisher":"publisher",         
            "releasedate":"release_date",       
            "releaseinfo":"release_information",
            "signlang":"sign_language",      
            "synopsis":"synopsis",          
            "episodetitle":"title_episode_title", 
            "title":"title_main",         
            "seriestitle":"title_series_title",  
            "height":"vertical_size",      
            "videocoding":"video_coding"
        }

tokens = (
          'AND', 'OR', 'NOT',
          'EQUALS', 'NEQUALS', 'LIKE', 'GT', 'LT', 'GTE', 'LTE',
          'LPAREN', 'RPAREN',
          #'IDENT', 'STRING',
          'STRING',
          'DIGIT', 'LETTER', 'QUOTMARK', 'OTHERCHAR'
          )

# Tokens
t_AND = r'\&\&'
t_OR = r'\|\|'
t_NOT = r'!'
t_EQUALS = r'=' 
t_NEQUALS = r'!='
t_LIKE = r'~'
t_GT = r'>'
t_LT = r'<'
t_GTE = r'>='
t_LTE = r'<='
t_LPAREN = r'\('
t_RPAREN = r'\)'
t_DIGIT = r'\d'
t_LETTER = r'[a-zA-Z]'
t_QUOTMARK = r'"'
t_OTHERCHAR = r'[ .,\-\\+\\*\\\'#:]' #allows: <space> . , - + * # :'
t_STRING = r'('+t_LETTER + r'|'+t_DIGIT +r'|' +t_OTHERCHAR +r')+'
t_ignore = ' \t'

def t_error(t):
    if DEBUG:
        print >>sys.stderr,"Illegal character '%s'" %t.value[0]
    t.lexer.skip(1)

# Precedence rules
precedence = (
    ('left', 'AND', 'OR'),
    ('right', 'NOT'),
    ('left', 'EQUALS', 'NEQUALS', 'LIKE', 'GT', 'LT', 'LTE', 'GTE'),
)

def p_expression_paren(p):
    'expression : LPAREN expression RPAREN'
    p[0] = "(" +p[2] +")"
    
def p_expression_not(p):
    'expression : NOT expression'
    p[0] = "NOT "+p[2]
    
def p_expression_conj(p):
    '''expression : expression AND expression
                  | expression OR expression
    '''
    if p[2] == "&&":
        p[0] = p[1] +" AND " +p[3]
    else:
        p[0] = p[1] +" OR " +p[3]
    
def p_expression_eval(p):
    '''expression : STRING EQUALS STRING 
                  | STRING NEQUALS STRING
                  | STRING LIKE STRING 
                  | STRING GT  STRING
                  | STRING LT STRING 
                  | STRING GTE STRING 
                  | STRING LTE STRING 
    '''
    column = fieldToColumnDict[p[1].strip().lower()]
    op = ''
    val = ''
    if p[2] == '~':
        op = ' LIKE '
        val = '%' +p[3] +'%'
    else:
        op = p[2]
        val = p[3]
        
    if column is not None:
        # store original value in fields list and use ? instead (against SQL insertions)
        fields.append(val)
        p[0] = column + op +"?"
    else :
        if DEBUG:
            print >>sys.stderr,"Field %s not known."%p[1]
    



def p_error(p):
    print >>sys.stderr,"Syntax error at '%s'" % p.value

def generateSQLQuery(query, querycols=['torrent_id'],maxhits=0):
    del fields[:] # empty previously stored fields
    if len(query) == 0:
        return None,None
    # Create Lexer and Parser
    lex.lex()
    if DEBUG:
        yacc.yacc()
    else: # do not create parser.out and other debug output
        yacc.yacc(debug=0)
    lex.input(query)
#    if DEBUG:
#        while 1:
#            tok = lex.token()
#            if not tok:
#                break
#            print >>sys.stderr, tok
    try:
        sqlcond = yacc.parse(query)
    except: # parser error, usually caused by a malformed query
        print >>sys.stderr, "Query could not be parsed."
        return "SELECT torrent_id FROM richmetadata WHERE 0", fields
    
     
    sql = "SELECT "+",".join(querycols) +" FROM richmetadata WHERE " +sqlcond
    
    if maxhits > 0:
        sql += " LIMIT "+str(maxhits) +";"
    else:
        sql += ";"
    if DEBUG:
        print >>sys.stderr, sql, fields
    return sql,fields
