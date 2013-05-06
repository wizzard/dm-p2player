# Written by Vladimir Jovanovikj
# see LICENSE.txt for license information

class EnhancedClosedSwarmsException(Exception):
    """ Super class for all ECS-specific Exceptions 
    """
    def __init__(self, msg=None):
        Exception.__init__(self,msg)

    def __str__(self):
        return str(self.__class__) + ': ' + Exception.__str__(self)
 

class InvalidRulesSintaxException(EnhancedClosedSwarmsException):
    """ The given rules have invlaid sintax.
    """
    def __init__(self, msg=None):
        EnhancedClosedSwarmsException.__init__(self,msg)

class EPOAExpiredException(EnhancedClosedSwarmsException):
    """ The EPOA has expired.
    """
    def __init__(self, msg=None):
        EnhancedClosedSwarmsException.__init__(self,msg)

class InvalidEPOAException(EnhancedClosedSwarmsException):
    """ Invalid EPOA.
    """
    def __init__(self, msg=None):
        EnhancedClosedSwarmsException.__init__(self,msg)

class InvalidMessageException(EnhancedClosedSwarmsException):
    """ Invalid message.
    """
    def __init__(self, msg=None):
        EnhancedClosedSwarmsException.__init__(self,msg)
