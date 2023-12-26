class CounterContext:
    count = -1      # so that first number is 0

    def __enter__(self):
        self._increment()
    async def __aenter__(self):
        self.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

    def _increment(self):
        self.count += 1
    def get_next(self):
        self._increment()
        return self.count