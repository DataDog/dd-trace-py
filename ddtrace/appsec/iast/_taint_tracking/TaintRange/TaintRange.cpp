//
// Created by alberto.vara on 16/05/23.
//

#include "TaintRange.h"

#include <sstream>

void TaintRange::reset() {
  position = 0;
  length = 0;
  // TODO: reset input info
  //        inputinfo = "";
};

const char *TaintRange::toString() const {
  ostringstream ret;
  ret << "TaintRange at " << this << " "
      << " [position=" << position << ", length=" << length
      << " input_info=" << inputinfo.toString() << "]";
  return ret.str().c_str();
}

TaintRange::operator std::string() const { return toString(); }

size_t TaintRange::get_hash() const {
  size_t hposition = hash<size_t>()(this->position);
  size_t hlength = hash<size_t>()(this->length);
  size_t hinputinfo = hash<size_t>()(this->inputinfo.get_hash());
  return hposition ^ hlength ^ hinputinfo;
};