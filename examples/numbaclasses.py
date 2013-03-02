# -*- coding: utf-8 -*-
"""
Example for extension classes.

Things that work:

    - overriding Numba methods in Numba (all methods are virtual)
    - inheritance
    - instance attributes
    - subclassing in python and calling overridden methods in Python
    - arbitrary new attributes on extension classes and objects
    - weakrefs to extension objects

Things that do NOT (yet) work:

    - overriding methods in Python and calling the method from Numba
    - multiple inheritance of Numba classes
        (multiple inheritance with Python classes should work)
    - subclassing variable sized objects like 'str' or 'tuple'
"""
from __future__ import print_function, division, absolute_import

from numba import jit, void, int_, double

# All methods must be given signatures

@jit
class Shrubbery(object):
    @void(int_, int_)
    def __init__(self, w, h):
        # All instance attributes must be defined in the initializer
        self.width = w
        self.height = h

        # Types can be explicitly specified through casts
        self.some_attr = double(1.0)

    @int_()
    def area(self):
        return self.width * self.height

    @void()
    def describe(self):
        print("This shrubbery is ", self.width,
              "by", self.height, "cubits.")
 
shrub = Shrubbery(10, 20)
print(shrub.area())
shrub.describe()
print(shrub.width, shrub.height)
shrub.width = 30
print(shrub.area())
print(shrub._numba_attrs._fields_) # This is an internal attribute subject to change!

class MyClass(Shrubbery):
    def newmethod(self):
        print("This is a new method.")

shrub2 = MyClass(30,40)
shrub2.describe()
shrub2.newmethod()
print(shrub._numba_attrs._fields_)


