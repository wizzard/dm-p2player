# Written by Boxun Zhang
# see LICENSE.txt for license information

import os
from time import time
import threading
from traceback import print_exc

from Tribler.__init__ import LIBRARYNAME
from Tribler.Core.CacheDB.sqlitecachedb import *
from Tribler.Core.CacheDB.SqliteCacheDBHandler import BasicDBHandler
from Tribler.Core.simpledefs import *

CREATE_USEREVENTLOG_SQL_FILE = None
CREATE_USEREVENTLOG_SQL_FILE_POSTFIX = os.path.join(LIBRARYNAME, 'Core', 'Statistics', 'tribler_usereventlog_sdb.sql')
DB_FILE_NAME = 'tribler_usereventlog.sdb'
DB_DIR_NAME = 'sqlite'    # db file path = DB_DIR_NAME/DB_FILE_NAME
CURRENT_DB_VERSION = 1
DEFAULT_BUSY_TIMEOUT = 10000
MAX_SQL_BATCHED_TO_TRANSACTION = 1000   # don't change it unless carefully tested. A transaction with 1000 batched updates took 1.5 seconds
SHOW_ALL_EXECUTE = False
costs = []
cost_reads = []

DEBUG = False

def init_usereventlog(config, db_exception_handler = None):
    """ create UserEventLog database """
    global CREATE_USEREVENTLOG_SQL_FILE
    config_dir = config['state_dir']
    install_dir = config['install_dir']
    CREATE_USEREVENTLOG_SQL_FILE = os.path.join(install_dir,CREATE_USEREVENTLOG_SQL_FILE_POSTFIX)
    sqlitedb = SQLiteUserEventLogCacheDB.getInstance(db_exception_handler)   
    sqlite_db_path = os.path.join(config_dir, DB_DIR_NAME, DB_FILE_NAME)
    sqlitedb.initDB(sqlite_db_path, CREATE_USEREVENTLOG_SQL_FILE)  # the first place to create db in Tribler
    return sqlitedb

class SQLiteUserEventLogCacheDB(SQLiteCacheDBBase):
    __single = None    # used for multi-threaded singletons pattern
    lock = threading.RLock()
    
    @classmethod
    def getInstance(cls, *args, **kw):
        # Singleton pattern with double-checking to ensure that it can only create one object
        if cls.__single is None:
            cls.lock.acquire()   
            try:
                if cls.__single is None:
                    cls.__single = cls(*args, **kw)
            finally:
                cls.lock.release()
        return cls.__single

    def __init__(self, *args, **kw):
        # always use getInstance() to create this object
        if self.__single != None:
            raise RuntimeError, "SQLiteUserEventLogCacheDB is singleton"
        
        SQLiteCacheDBBase.__init__(self, *args, **kw)
    
    

class UserEventLogDBHandler(BasicDBHandler):
    """
    The database handler for logging user events.
    """
    __single = None    # used for multithreaded singletons pattern
    lock = threading.Lock()
    
    # maximum number of events to store
    # when this maximum is reached, approx. 50% of teh entries are deleted.
    MAX_EVENTS = 2*10000
    
    def getInstance(*args, **kw):
        # Singleton pattern with double-checking
        if UserEventLogDBHandler.__single is None:
            UserEventLogDBHandler.lock.acquire()   
            try:
                if UserEventLogDBHandler.__single is None:
                    UserEventLogDBHandler(*args, **kw)
            finally:
                UserEventLogDBHandler.lock.release()
        return UserEventLogDBHandler.__single
    getInstance = staticmethod(getInstance)
    
    def __init__(self):
        if UserEventLogDBHandler.__single is not None:
            raise RuntimeError, "UserEventLogDBHandler is singleton"
        UserEventLogDBHandler.__single = self
        db = SQLiteUserEventLogCacheDB.getInstance()      
        BasicDBHandler.__init__(self,db, 'UserEventLog')
        
        self.count = self._db.size(self.table_name)
    
    def addEvent(self, message, type=1, timestamp=None):
        """
        Log a user event to the database. Commits automatically.
        
        @param message A message (string) describing the event.
        @param type Optional type of event (default: 1). There is no
        mechanism to register user event types.
        @param timestamp Optional timestamp of the event. If omitted,
        the current time is used.
        """
        if timestamp is None:
            timestamp = time()
        self._db.insert(self.table_name, commit=False,
                        timestamp=timestamp, type=type, message=message)
        
        self.count += 1
        if self.count > UserEventLogDBHandler.MAX_EVENTS:
            sql=\
            '''
            DELETE FROM UserEventLog
            WHERE timestamp < (SELECT MIN(timestamp)
                               FROM (SELECT timestamp
                                     FROM UserEventLog
                                     ORDER BY timestamp DESC LIMIT %s))
            ''' % (UserEventLogDBHandler.MAX_EVENTS / 2)
            self._db.execute_write(sql, commit=True)
            self.count = self._db.size(self.table_name)
        else:
            self._db.commit()
            
        
