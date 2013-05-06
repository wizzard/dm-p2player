# Written by Vladimir Jovanovikj
# see LICENSE.txt for license information
from traceback import print_exc

from Tribler.Core.CacheDB import yacc
from Tribler.Core.CacheDB import lex

import sys
import time
from types import ListType
try:
    import pygeoip
except:
    #print >>sys.stderr,"ECS_AuthorizationEngine: Enhanced Closed Swarms requires pygeoip to be installed"
    pass

from Tribler.Core.ClosedSwarm.ECS_Exceptions import *
from Tribler.Core.ClosedSwarm.conf import ecssettings

import urllib2


def getExternalIP():
    ip = urllib2.urlopen("http://whatismyip.org").read()
    return ip


class ECS_Lexer:
    '''
    This class implements a lexer for extracting variable names and values 
    according to the ECS specific grammar. It is applied on the contents from 
    Rules and ReqService fields.
    '''
    def __init__(self):
        '''
        Initialize an {@link ECS_Lexer} object
        '''
        self.lexer = lex.lex(module=self)
        
    def get_variable_name(self, text):
        '''
        Extract the variable name from a given text. It acts as verifier whether
        the provided text is a valid variable name.

        @param text Given text to extract the variable name from
        @return Extracted variable name
        '''
        self.lexer.input(str(text))
        var_name = self.lexer.token()
        if not (var_name.type == 'VAR' and len(var_name.value) == len(str(text))): # can be without str since all are strings if type is var
            raise InvalidRulesSintaxException("Syntax error: '%s' is an invalid variable name!" % var_name.value)

        return var_name.value

    def get_variable_value(self, text):
        '''
        Extract the variable value from a given text. It acts as verifier 
        whether the provided text is a valid variable value.

        @param text Given text to extract the variable value from
        @return Extracted variable value
        '''
        self.lexer.input(str(text))
        var_value = self.lexer.token()
        # Maybe check all var names whether they have correct value type
        if not (var_value.type == 'NUMBER' and len(str(var_value.value)) == len(str(text)) or var_value.type == 'STRING' and len(var_value.value) + 2 == len(text)):
            raise InvalidRulesSintaxException("Syntax error: %s is an invalid variable value!" % var_value.value)

        return var_value.value

    '''
    The rest of this class's code is the actual lexer functionality, written 
    according to the specifications for the lex.py module. For extensive 
    explanation, please refer to PLY(Python Lex-Yacc) documentation:
    www.dabeaz.com/ply/ply.html
    '''

    # Tokens
    tokens = (
              'AND', 'OR',
              'EQUALS', 'NEQUALS', 'GT', 'LT', 'GTE', 'LTE',
              'LPAREN', 'RPAREN', 
              'NUMBER', 'STRING', 
              'VAR'
              )

    t_AND = r'\&\&'
    t_OR = r'\|\|'
    t_EQUALS = r'=' 
    t_NEQUALS = r'!='
    t_GT = r'>'
    t_LT = r'<'
    t_GTE = r'>='
    t_LTE = r'<='
    t_LPAREN = r'\('
    t_RPAREN = r'\)'

    def t_NUMBER(self, t):
        r'[0-9]{1,10}\.[0-9]{1}|[0-9]{1,10}'
        if '.' in t.value:
            t.value = float(t.value)
        else:
            t.value = int(t.value)
        return t

    def t_STRING(self, t):
        r'\'[a-zA-z_]{1,10}\''
        t.value = str(t.value)
        t.value = t.value[1:-1]
        return t

    def t_VAR(self, t):
        r'[A-Za-z]{1}[a-zA-z_0-9]{0,20}'
        return t

    t_ignore = ' \t'

    def t_newline(self, t):
        r'\n+'
        t.lexer.lineno += len(t.value)

    def t_error(self, t):
        t.lexer.skip(1)
        raise InvalidRulesSintaxException("Syntax error in lexer!")


def parse_reqservice(reqservice):
    '''
    Parse given ReqService field.

    @param reqservice Requested service properties by a node. The type of 
    this field is a list of 2 element lists, each specifying a pair: 
    (variable name, varuable value).
    @return Dictionary with the parsed data. The format of the dictionary:
    key: variable name; value: variable value.
    '''
    rs_lexer = ECS_Lexer()
    output = {}
    if type(reqservice) != ListType:
        raise InvalidRulesSintaxException("Invalid ReqService field: Type error: Not a list of 2 element lists")
    elif len(reqservice) > 0:
        for i in reqservice:
            if not (type(i) == ListType and len(i) == 2):
                raise InvalidRulesSintaxException("Invalid ReqService field: Type error: Not a list of 2 element lists")
    for rs in reqservice:
        var_name = rs_lexer.get_variable_name(rs[0])
        if var_name in ecssettings.FORBIDDEN_VARS:
            raise InvalidRulesSintaxException("Invalid ReqService field: Syntax error: '%s' cannot be requested!" % var_name)
        var_value = rs_lexer.get_variable_value(rs[1])
        output[var_name] = var_value
    return output


class ECS_Parser:
    '''
    This class implements a parser of group of conditions according to the ECS 
    specific grammar. It is applied on the content from the Rules field. The 
    evaluation of the conditions in the Rules field is done while parsing.
    Therefore, this class also acts as a Rules field evaluator.
    '''
    def __init__(self):
        self.environment = None
        '''The following attribute keeps values of DAY_HOUR variables that are 
        part of conditions evaluated to True, but havent passed yet''' 
        self.dayhour_yet = []
        '''The following attribute keeps variable names that are part of 
        conditions evaluated to True'''
        self.varname_true = []
        self.max_priority = 0
        self.parser = yacc.yacc(module=self, write_tables=0)
        self.evaluate = None
        self.environment = None
        self.parsed = False

    def parse_rules(self, rules, environment=None, evaluate=True):
        '''
        Parse given rules. If specified, evaluate them at the same time and
        return the result. Otherwise, only parse them.

        @param rules Rules field content
        @param environment Environment in which the rules are evaluated
        @param evaluate Flag to specify whether to evaluate or not        
        @return Boolean
        '''
        self.evaluate = evaluate
        if self.evaluate:
            self.environment = environment
        result = self.parser.parse(rules, ECS_Lexer().lexer)
        self.parsed = True
        if evaluate:
            return result

    '''
    The rest of this class's code is the actual parser functionality, written 
    according to the specifications for the yacc.py module. For extensive 
    explanation, please refer to PLY(Python Lex-Yacc) documentation:
    www.dabeaz.com/ply/ply.html
    '''

    '''
    The rules:

    conditions : condition
               | conditions AND conditions
               | conditions OR conditions
               | LPAREN conditions RPAREN

    condition : VAR EQUALS value
              | VAR NEQUALS value
              | VAR GT value
              | VAR LT value
              | VAR GTE value
              | VAR LTE value

    value : NUMBER | STRING | VAR
    '''

    # Tokens
    tokens = (
              'AND', 'OR',
              'EQUALS', 'NEQUALS', 'GT', 'LT', 'GTE', 'LTE',
              'LPAREN', 'RPAREN', 
              'NUMBER', 'STRING', 
              'VAR'
              )


    def p_conditions_single(self, p):
        'conditions : condition'
        p[0] = p[1]

    def p_conditions_and(self, p):
        'conditions : conditions AND conditions'
        p[0] = p[1] and p[3]
        # if ecssettings.DEBUG_AE:
        #     print >> sys.stderr, p[0],'=',p[1],'and',p[3]

    def p_conditions_or(self, p):
        'conditions : conditions OR conditions'
        p[0] = p[1] or p[3]
        # if ecssettings.DEBUG_AE:
        #     print >> sys.stderr, p[0],'=',p[1],'or',p[3]

    def p_conditions_paren(self, p):
        'conditions : LPAREN conditions RPAREN'
        p[0] = p[2]

    def p_condition_equals(self, p):
        'condition : VAR EQUALS value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] == p[3]
            if p[0]:
                self.varname_true.append(p[1])
            if p[1] == ecssettings.DAY_HOUR:
                assert type(p[3]) == int
                d1 = p[3] / 100
                d2 = p[3] % 100
                assert d1 <= 7, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                assert d2 <= 24, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                d3 = self.environment[p[1]] / 100
                d4 = self.environment[p[1]] % 100
                #if self.environment[p[1]] <= p[3]:
                if self.environment[p[1]] == p[3]:
                    self.dayhour_yet.append((d1-d3)*24+d2-d4+1)
        if p[1] == ecssettings.PRIORITY:
            assert type(p[3]) == int
            self.max_priority = p[3]

    def p_condition_nequals(self, p):
        'condition : VAR NEQUALS value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] != p[3]
            if p[0]:
                self.varname_true.append(p[1])

    def p_condition_gt(self, p):
        'condition : VAR GT value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] > p[3]
            if p[0]:
                self.varname_true.append(p[1])

    def p_condition_lt(self, p):
        'condition : VAR LT value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] < p[3]
            if p[0]:
                self.varname_true.append(p[1])
            if p[1] == ecssettings.DAY_HOUR:
                assert type(p[3]) == int
                d1 = p[3] / 100
                d2 = p[3] % 100
                assert d1 <= 7, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                assert d2 <= 24, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                d3 = self.environment[p[1]] / 100
                d4 = self.environment[p[1]] % 100
                if self.environment[p[1]] < p[3]:
                    self.dayhour_yet.append((d1-d3)*24+d2-d4)
        if p[1] == ecssettings.PRIORITY:
            assert type(p[3]) == int
            self.max_priority = p[3] - 1

    def p_condition_gte(self, p):
        'condition : VAR GTE value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] >= p[3]
            if p[0]:
                self.varname_true.append(p[1])

    def p_condition_lte(self, p):
        'condition : VAR LTE value'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]] <= p[3]
            if p[0]:
                self.varname_true.append(p[1])
            if p[1] == ecssettings.DAY_HOUR:
                assert type(p[3]) == int, "DAY_HOUR variable is of type integer not '%s'" % type(p[3])
                d1 = p[3] / 100
                d2 = p[3] % 100
                assert d1 <= 7, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                assert d2 <= 24, "%d is an invalid value for a DAY_HOUR variable" % p[3]
                d3 = self.environment[p[1]] / 100
                d4 = self.environment[p[1]] % 100
                if self.environment[p[1]] <= p[3]:
                    self.dayhour_yet.append((d1-d3)*24+d2-d4+1)
        if p[1] == ecssettings.PRIORITY:
            assert type(p[3]) == int
            self.max_priority = p[3]

    def p_value_number(self, p):
        'value : NUMBER'
        p[0] = p[1]

    def p_value_string(self, p):
        'value : STRING'
        p[0] = p[1]

    def p_value_var(self, p):
        'value : VAR'
        if self.evaluate:
            assert p[1] in self.environment, "Error in Rules: Environment variable '%s' does not exist!" % p[1]
            p[0] = self.environment[p[1]]

    def p_error(self, p):
        raise InvalidRulesSintaxException("Syntax error in parser!")

    precedence = (
        ('nonassoc', 'EQUALS', 'NEQUALS', 'GT', 'LT', 'LTE', 'GTE'),
        ('left', 'OR'),
        ('left', 'AND'),
    )


class Rules:
    '''
    This class is {@link ECS_Parser} wrapper
    '''
    def __init__(self, rules):
        self.rules =  rules
        self.environment = None
        '''The following attribute contains values of DAY_HOUR variables that 
        are part of conditions evaluated to True, but havent passed yet''' 
        self.dayhour_yet = []
        '''The following attribute contains variable names that are part of 
        conditions evaluated to True'''
        self.varname_true = []
        self.max_priority = 0
        self.parser = ECS_Parser()

    def evaluate_rules(self, environment):
        '''
        Evaluate rules with respect to given environment.

        @param environment Given environment
        @return Boolean
        '''
        assert environment is not None, "Environment needed for evaluating the rules"
        self.environment = environment
        result = self.parser.parse_rules(self.rules, self.environment)
        self.dayhour_yet = self.parser.dayhour_yet 
        self.varname_true = self.parser.varname_true
        self.max_priority = self.parser.max_priority
        return result

    def pre_check_rules(self):
        '''
        Parse rules and extract specific variable values. Currently only maximum
        priority is needed.

        @return Integer
        '''
        result = self.parser.parse_rules(self.rules, evaluate=False)
        self.max_priority = self.parser.max_priority


def get_max_priority(remote_rules):
    '''
    Return maximum priority value from given rules.
    
    @param remote_rules Given rules
    @return Integer
    '''
    rules = Rules(remote_rules)
    rules.pre_check_rules()
    return rules.max_priority

def get_max_reqservice(rules):
    '''
    Create ReqService field content with maximum service properties that can be 
    received as specified in the content of a given Rules field.

    @param rules Given rules
    '''
    max_priority = get_max_priority(rules)
    max_reqservice = [[ecssettings.PRIORITY, str(max_priority)]]
    return max_reqservice

class Authorization_Engine:
    '''
    This class implements functionality for coordinating the process of 
    evaluation of the Rules field contents of different peers from different 
    swarms. 
    '''
    def __init__(self):
        self.environment = {}
        '''Dict format: key: ecs_connection; value:  { key: PRIORITY;    value:...,
                                                       key: GEOLOCATION; value:...,
                                                       key: DAY_HOUR;    value:...
        '''
    def set_environment(self, ecs_connection, remote_reqservice=None):
        '''
        Set environment for rules evaluation for given {@link ECS_Connection} 
        object. The environment is set from: the given ReqService field content,
        the output of geolocation call, and from the current time.
        
        @param ecs_connection Given {@link ECS_Connection} object
        @param remote_reqservice Given ReqService field content
        '''
        # Set environment variables from ReqService
        if remote_reqservice is not None:
            rs_parsed = parse_reqservice(remote_reqservice)
            self.environment[ecs_connection] = rs_parsed

        # Set other environment variables
        ip = ecs_connection.connection.get_ip()
        if ip == "127.0.0.1":
            country_code = "SI"
        else:
            gi = pygeoip.GeoIP(ecssettings.GEOIP_DB)
            country_code = gi.country_code_by_addr(ip)
        self.environment[ecs_connection][ecssettings.GEOLOCATION] = country_code

        now = time.gmtime()
        now_day_hour = (now.tm_wday + 1) * 100 + now.tm_hour
        self.environment[ecs_connection][ecssettings.DAY_HOUR] = now_day_hour

    def update_environment(self, ecs_connection):
        '''
        Update environment for given {@link ECS_Connection} object.

        @param ecs_connection Given {@link ECS_Connection} object
        '''
        now = time.gmtime()
        now_day_hour = (now.tm_wday + 1) * 100 + now.tm_hour
        self.environment[ecs_connection][ecssettings.DAY_HOUR] = now_day_hour


    def _evaluate_rules(self, ecs_connection, remote_rules, remote_reqservice=None): # change name to authorization_decision?
        '''
        Evaluate rules with respect to given environment, for given 
        {@link ECS_Connection} object.

        @param ecs_connection Given {@link ECS_Connection} object
        @param remote_rules Given Rules field content
        @return Tuple
        '''
        # Evaluate rules
        self.update_environment(ecs_connection)
        rules = Rules(remote_rules)
        result = rules.evaluate_rules(self.environment[ecs_connection])

        # Determine time of next rules evaluations
        '''minDH contains number of hours till when peer is allowed to receive content'''
        minDH = None
        if result and rules.dayhour_yet:
            minDH = min(rules.dayhour_yet)
            minDH = minDH / 100 * 24 + minDH % 100
        
        # Determine whether remote peer requested services according to authorizations
        authorized_service_properties_requested = True
        if remote_reqservice is not None:
            for i in remote_reqservice:
                if not (i[0] in rules.varname_true):
                    authorized_service_properties_requested = False

        return result, authorized_service_properties_requested, minDH

    def evaluate_rules(self, ecs_connection, remote_rules, remote_reqservice):
        '''
        Wrapper for the above defined method
        '''
        try:
            result, authorized_service_properties_requested, minDH = self._evaluate_rules(ecs_connection, remote_rules, remote_reqservice)
        except (InvalidRulesSintaxException, AssertionError, Exception) as e:
            if ecssettings.DEBUG_AE:
                print >> sys.stderr, "Authorization Engine Exception:", e
            result, authorized_service_properties_requested, minDH = None, None, None
        return result, authorized_service_properties_requested, minDH
