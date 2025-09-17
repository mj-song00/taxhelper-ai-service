package taxhelper.chatservice.domain.law;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.JsonNode;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class StringOrArrayDeserializer extends JsonDeserializer<List<String>> {

    @Override
    public List<String> deserialize(JsonParser p, DeserializationContext ctxt) throws IOException {
        JsonNode node = p.getCodec().readTree(p);

        if (node.isArray()) {
            List<String> list = new ArrayList<>();
            for (JsonNode n : node) {
                list.add(n.asText());
            }
            return list;
        } else if (node.isTextual()) {
            return List.of(node.asText());
        }
        return Collections.emptyList();
    }
}
