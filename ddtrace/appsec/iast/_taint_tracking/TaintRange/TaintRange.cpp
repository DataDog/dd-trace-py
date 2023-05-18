//
// Created by alberto.vara on 16/05/23.
//

#include "TaintRange.h"

#include <sstream>

void TaintRange::reset() {
  start = 0;
  length = 0;
  if (source) {
        Py_XDECREF(source);
        delete source;
        source = nullptr;
  }
};

const char *TaintRange::toString() const {
  ostringstream ret;
  ret << "TaintRange at " << this << " "
      << " [start=" << start << ", length=" << length
      << " source=" << source->toString() << "]";
  return ret.str().c_str();
}

TaintRange::operator std::string() const { return toString(); }

size_t TaintRange::get_hash() const {
  size_t hstart = hash<size_t>()(this->start);
  size_t hlength = hash<size_t>()(this->length);
  size_t hsource = hash<size_t>()(this->source->get_hash());
  return hstart ^ hlength ^ hsource;
};


// TODO: equals()
// TODO: isValid()
// TODO: shift()