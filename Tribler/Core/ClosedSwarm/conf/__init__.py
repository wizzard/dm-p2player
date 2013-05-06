import EnhancedClosedSwarmSettings

class Settings():

    def __init__(self):
        self.conf = None

    def __getattr__(self, name):
        if self.conf is None:
            # Delayed initialization
            self.__import_settings()
        return getattr(self.conf, name)

    def __setattr__(self, name, value):
        if name == 'conf':
            # Treat conf differently to avoid infinite loop
            self.__dict__['conf'] = value
        else:
            if self.conf is None:
                self.__import_settings()
            setattr(self.conf, name, value)

    def __getitem__(self, name):
        return self.__getattr__(name)

    def __import_settings(self):
        i = ImportConfig()
        self.conf = i.getConfig()

    def list(self):
        if self.conf is None:
            self.__import_settings()
        out = ""
        for setting in dir(self.conf):
            if setting == setting.upper():
                out += setting + " = " + str(self.__getitem__(setting)) + "\n"
        return out

class ImportConfig():

    def __init__(self):
        self.config = EnhancedClosedSwarmSettings

    def getConfig(self):
        for setting in dir(self.config):
            # Only upper case attributes are considered as settings
            if setting == setting.upper():
                setattr(self, setting, getattr(self.config, setting))
        # Store where the settings were loaded from
        setattr(self, "SETTINGS", self.config)
        setattr(self, "SETTINGS_NAME", self.config.__name__)
        return self

ecssettings = Settings()
